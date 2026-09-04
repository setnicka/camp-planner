"""The global warehouse: permissions, boxes, items, inventory checks, photos."""

from __future__ import annotations

import io
import re

import pytest
from sqlalchemy import exc as sa_exc
from sqlalchemy.orm.attributes import set_committed_value

from camp_planner.config import MAX_UPLOAD_BYTES
from camp_planner.extensions import db, db_session
from camp_planner.models.audit import AuditAction, AuditLog, EntityType
from camp_planner.models.inventory import InventoryBox, InventoryCheck, InventoryCheckRecord
from camp_planner.services import errors, inventory, media
from tests.conftest import (
    ADMIN,
    box_state,
    complete,
    discard,
    editor,
    get_item,
    make_box,
    make_item,
    observe,
    page_data,
    png,
    start_check,
    upload,
    viewer,
)

# An editor of *some* camp may edit the warehouse; it belongs to no camp.
ED = editor("t")
VI = viewer("t")


@pytest.fixture
def box(client):
    return make_box(client)


def test_anonymous_is_rejected(client):
    assert client.get("/inventory").status_code == 401
    assert client.post("/api/inventory/boxes", json={"name": "X"}).status_code == 401


def test_viewer_reads(client, seeded):
    assert client.get("/inventory", headers=VI).status_code == 200


def test_every_write_endpoint_is_guarded(client, seeded):
    """A real viewer grant (seeded makes the camp exist) is still no write. The guard runs
    before the lookup, so a viewer gets 403 for any id. The bodies pass the schema, which
    spectree checks before the view runs."""
    writes = [
        ("post", "/api/inventory/boxes", {"name": "X"}), ("patch", "/api/inventory/boxes/1", {}),
        ("delete", "/api/inventory/boxes/1", None),
        ("post", "/api/inventory/items", {"name": "X", "box_id": 1}),
        ("patch", "/api/inventory/items/1", {}), ("delete", "/api/inventory/items/1", None),
        ("post", "/api/inventory/items/1/discard", None),
        ("post", "/api/inventory/items/1/restore", {"box_id": 1}),
        ("post", "/api/inventory/items/1/photos", None), ("delete", "/api/inventory/photos/1", None),
        ("post", "/api/inventory/photos/1/title", None),
        ("post", "/api/inventory/checks", {"name": "X"}),
        ("post", "/api/inventory/checks/1/complete", None), ("delete", "/api/inventory/checks/1", None),
        ("put", "/api/inventory/boxes/1/records/1", {}),
        ("delete", "/api/inventory/boxes/1/records/1", None),
    ]
    for method, url, body in writes:
        assert getattr(client, method)(url, json=body, headers=VI).status_code == 403, url


def test_editor_of_any_camp_may_write(client, seeded):
    """The grant needs its camp to exist: a slug nobody knows grants nothing."""
    resp = client.post("/api/inventory/boxes", json={"name": "Krabice E"}, headers=ED)
    assert resp.status_code == 200, resp.get_json()
    resp = client.post("/api/inventory/boxes", json={"name": "Krabice N"}, headers=editor("nope"))
    assert resp.status_code == 403


@pytest.mark.parametrize("role, status", [("editor", 200), ("viewer", 403)])
def test_api_token_counts_by_its_own_role(client, seeded, role, status):
    """Tokens are camp-scoped, but the warehouse is shared: the role is what counts."""
    read = make_box(client, "Ke čtení")
    secret = client.post("/api/camps/t/tokens", json={"name": "t", "role": role},
                         headers=ADMIN).get_json()["secret"]
    token = {"Authorization": f"Bearer {secret}"}
    resp = client.post("/api/inventory/boxes", json={"name": "Z tokenu"}, headers=token)
    assert resp.status_code == status, resp.get_json()
    assert client.get(f"/api/inventory/boxes/{read['id']}/state", headers=token).status_code == 200


# --- boxes -------------------------------------------------------------------

def test_box_names_are_unique(client, box):
    resp = client.post("/api/inventory/boxes", json={"name": "Krabice 1"}, headers=ADMIN)
    assert resp.status_code == 400
    assert "už existuje" in resp.get_json()["error"]


def test_rename_to_a_taken_name_is_refused_and_keeps_the_old_one(client, box):
    other = make_box(client, "Krabice 2")
    resp = client.patch(f"/api/inventory/boxes/{other['id']}",
                        json={"name": box["name"]}, headers=ADMIN)
    assert resp.status_code == 400
    assert "existuje" in resp.get_json()["error"]
    assert box_state(client, other["id"])["box"]["name"] == "Krabice 2"


def test_virtual_flag_round_trips(client, box):
    assert box["virtual"] is False
    made = make_box(client, "Volně v A0", virtual=True)
    assert made["virtual"] is True
    patched = client.patch(f"/api/inventory/boxes/{made['id']}", json={"virtual": False},
                           headers=ADMIN).get_json()["box"]
    assert patched["virtual"] is False


def test_box_with_items_cannot_be_deleted(client, box):
    make_item(client, box["id"])
    resp = client.delete(f"/api/inventory/boxes/{box['id']}", headers=ADMIN)
    assert resp.status_code == 400
    assert "jsou v ní věci" in resp.get_json()["error"]


def test_the_db_refuses_to_delete_a_box_with_items(client, box):
    """The service checks first; the DB holds the rule for any other path."""
    item = make_item(client, box["id"])
    db_session.delete(db_session.get(InventoryBox, box["id"]))
    with pytest.raises(sa_exc.IntegrityError):
        db_session.flush()
    db_session.rollback()
    assert get_item(client, item["id"])["box_id"] == box["id"]


def test_empty_box_is_deleted(client, box):
    assert client.delete(f"/api/inventory/boxes/{box['id']}", headers=ADMIN).status_code == 200


def test_box_holding_an_observation_cannot_be_deleted(client, box):
    """Somebody put a thing here during the running check; deleting the box would lose it."""
    other = make_box(client, "Krabice 2")
    item = make_item(client, box["id"])
    start_check(client)
    assert observe(client, other["id"], item["id"], box_id=other["id"]).status_code == 200
    resp = client.delete(f"/api/inventory/boxes/{other['id']}", headers=ADMIN)
    assert resp.status_code == 400
    assert "inventura" in resp.get_json()["error"]


def test_deleting_a_box_leaves_its_finished_records_in_a_nameless_group(client, box):
    """box_id of finished records is SET NULL; the check page groups them as deleted."""
    target = make_box(client, "Zrušená")
    item = make_item(client, box["id"])
    check = start_check(client)
    observe(client, box["id"], item["id"], box_id=target["id"])
    complete(client, check)
    # Move the item back out so the box is empty and deletable.
    client.patch(f"/api/inventory/items/{item['id']}", json={"box_id": box["id"]},
                 headers=ADMIN)
    assert client.delete(f"/api/inventory/boxes/{target['id']}",
                         headers=ADMIN).status_code == 200
    groups = page_data(client, f"/inventory/checks/{check['id']}")["groups"]
    assert [g["box"] for g in groups] == [None]
    assert groups[0]["records"][0]["item_id"] == item["id"]


def test_box_history_lists_completed_checks(client, box):
    item = make_item(client, box["id"])
    check = start_check(client)
    observe(client, box["id"], item["id"])
    complete(client, check)
    j = client.get(f"/api/inventory/boxes/{box['id']}/history", headers=VI).get_json()
    assert [c["id"] for c in j["checks"]] == [check["id"]]
    assert [r["item_id"] for r in j["records"]] == [item["id"]]


def test_pages_are_titled_after_their_box_and_check(client, box):
    """Several boxes open at once must tell their tabs apart."""
    html = client.get(f"/inventory/boxes/{box['id']}", headers=ADMIN).get_data(as_text=True)
    assert "<title>Krabice 1 – Sklad</title>" in html
    check = start_check(client, name="Jarní")
    assert complete(client, check).status_code == 200
    html = client.get(f"/inventory/checks/{check['id']}", headers=ADMIN).get_data(as_text=True)
    assert "<title>Jarní – Sklad</title>" in html


def test_box_page_knows_whether_history_mentions_it(client, box):
    """has_history greys out the Historie button; in_history warns before a delete that
    finished checks would lose the box's name. Both flags follow the finished checks only."""
    target = make_box(client, "Krabice 2")
    item = make_item(client, box["id"])
    url = f"/inventory/boxes/{target['id']}"
    check = start_check(client)
    observe(client, box["id"], item["id"], box_id=target["id"])
    before = page_data(client, url)
    assert (before["in_history"], before["has_history"]) == (False, False)
    complete(client, check)
    after = page_data(client, url)
    assert (after["in_history"], after["has_history"]) == (True, True)
    source = page_data(client, f"/inventory/boxes/{box['id']}")
    assert source["in_history"] is True        # the record's from_box_id leg
    assert source["has_history"] is False      # but nothing it lists was observed here


# --- items -------------------------------------------------------------------

def test_item_unit_and_count_may_be_empty(client, box):
    item = make_item(client, box["id"], name="PB bomba", count=None, unit=None)
    assert item["count"] is None and item["unit"] is None


def test_item_url_must_be_a_web_link(client, box):
    resp = client.post("/api/inventory/items", headers=ADMIN,
                       json={"name": "X", "box_id": box["id"], "url": "javascript:alert(1)"})
    assert resp.status_code == 422


@pytest.mark.parametrize("count", [float("inf"), float("nan"), -5])
def test_a_count_must_be_a_finite_amount(client, box, count):
    """json.dumps writes inf as the bare token Infinity, which no JSON parser reads back:
    one such row would blank every warehouse page for everybody."""
    item = make_item(client, box["id"])
    # 422: the schema refuses it, so it never reaches the service (nor the DB).
    assert client.post("/api/inventory/items", headers=ADMIN,
                       json={"name": "X", "box_id": box["id"], "count": count}
                       ).status_code == 422
    assert client.patch(f"/api/inventory/items/{item['id']}", headers=ADMIN,
                        json={"count": count}).status_code == 422
    start_check(client)
    assert observe(client, box["id"], item["id"], count=count).status_code == 422


def test_an_item_must_be_in_a_box(client, box):
    item = make_item(client, box["id"])
    resp = client.patch(f"/api/inventory/items/{item['id']}", json={"box_id": None},
                        headers=ADMIN)
    assert resp.status_code == 400
    assert "krabici" in resp.get_json()["error"]


def test_discard_clears_the_box_and_restore_puts_it_back(client, box):
    item = make_item(client, box["id"])
    discarded = discard(client, item).get_json()["item"]
    assert discarded["discarded_at"] is not None and discarded["box_id"] is None
    assert "už vyřazená" in discard(client, item).get_json()["error"]

    restored = client.post(f"/api/inventory/items/{item['id']}/restore",
                           json={"box_id": box["id"]}, headers=ADMIN).get_json()["item"]
    assert restored["discarded_at"] is None and restored["box_id"] == box["id"]
    again = client.post(f"/api/inventory/items/{item['id']}/restore",
                        json={"box_id": box["id"]}, headers=ADMIN)
    assert again.status_code == 400 and "není vyřazená" in again.get_json()["error"]


def test_discarded_item_cannot_be_moved_by_a_plain_edit(client, box):
    item = make_item(client, box["id"], name="Původní")
    discard(client, item)
    resp = client.patch(f"/api/inventory/items/{item['id']}",
                        json={"name": "Jiné", "box_id": box["id"]}, headers=ADMIN)
    assert resp.status_code == 400
    assert "Vrátit do skladu" in resp.get_json()["error"]
    # A refused move must not leave the rest of the patch applied: validate, then write.
    assert get_item(client, item["id"])["name"] == "Původní"


def test_hard_delete_removes_the_item(client, box):
    item = make_item(client, box["id"])
    assert client.delete(f"/api/inventory/items/{item['id']}", headers=ADMIN).status_code == 200
    assert client.patch(f"/api/inventory/items/{item['id']}", json={},
                        headers=ADMIN).status_code == 404


# --- check lifecycle ---------------------------------------------------------

def test_only_one_check_runs_at_a_time(client):
    start_check(client)
    resp = client.post("/api/inventory/checks", json={"name": "Druhá"}, headers=ADMIN)
    assert resp.status_code == 400
    assert "už probíhá" in resp.get_json()["error"]


def test_an_active_check_has_no_history_page(client):
    check = start_check(client)
    assert client.get(f"/inventory/checks/{check['id']}", headers=ADMIN).status_code == 404


def test_cancelling_a_check_drops_its_observations(client, box):
    item = make_item(client, box["id"], count=3)
    check = start_check(client)
    observe(client, box["id"], item["id"], count=99)
    assert client.delete(f"/api/inventory/checks/{check['id']}", headers=ADMIN).status_code == 200
    assert get_item(client, item["id"])["count"] == 3           # nothing propagated
    start_check(client, name="Druhá")                           # and a new one may start


def test_a_completed_check_is_frozen(client, box):
    """Cancelling is a rule (400); completing again is a lost race (409), the double
    click, and must not apply the observations twice: the note append would show it."""
    item = make_item(client, box["id"], note="Původní")
    check = start_check(client)
    observe(client, box["id"], item["id"], note="3 rozbité")
    complete(client, check)
    assert client.delete(f"/api/inventory/checks/{check['id']}",
                         headers=ADMIN).status_code == 400
    resp = complete(client, check)
    assert resp.status_code == 409
    assert "dokončená" in resp.get_json()["error"]
    assert get_item(client, item["id"])["note"].count("3 rozbité") == 1


# --- observation semantics ---------------------------------------------------

def test_unchecked_items_are_left_alone(client, box):
    item = make_item(client, box["id"], count=5, unit="ks")
    check = start_check(client)
    complete(client, check)
    after = get_item(client, item["id"])
    assert (after["count"], after["unit"]) == (5, "ks")


def test_new_observation_is_seeded_from_the_item(client, box):
    """"Stav sedí" is just an observation with nothing overridden."""
    item = make_item(client, box["id"], count=4, unit="ks")
    start_check(client)
    state = observe(client, box["id"], item["id"]).get_json()["state"]
    record = state["records"][0]
    assert (record["count"], record["unit"], record["box_id"]) == (4, "ks", box["id"])
    assert (state["checked"], state["total"]) == (1, 1)


def test_record_snapshots_the_source_box(client, box):
    item = make_item(client, box["id"])
    target = make_box(client, "Krabice 2")
    start_check(client)
    state = observe(client, box["id"], item["id"], box_id=target["id"]).get_json()["state"]
    record = state["records"][0]
    assert record["from_box_id"] == box["id"]
    assert record["box_id"] == target["id"]


def test_count_propagates_verbatim_including_null(client, box):
    """Clearing the number is a real observation ("we have it, uncounted"), not "no change"."""
    item = make_item(client, box["id"], count=4, unit="ks")
    check = start_check(client)
    observe(client, box["id"], item["id"], count=None, unit=None)
    complete(client, check)
    after = get_item(client, item["id"])
    assert after["count"] is None and after["unit"] is None


def test_adjusted_count_propagates(client, box):
    item = make_item(client, box["id"], count=4, unit="ks")
    check = start_check(client)
    observe(client, box["id"], item["id"], count=2.5, unit="m")
    complete(client, check)
    after = get_item(client, item["id"])
    assert (after["count"], after["unit"]) == (2.5, "m")


def test_discarded_observation_retires_the_item(client, box):
    item = make_item(client, box["id"], count=4)
    check = start_check(client)
    observe(client, box["id"], item["id"], discarded=True)
    complete(client, check)
    after = get_item(client, item["id"])
    assert after["discarded_at"] is not None and after["box_id"] is None


def test_discarded_observation_cannot_carry_a_count(client, box):
    item = make_item(client, box["id"])
    start_check(client)
    resp = observe(client, box["id"], item["id"], discarded=True, count=3)
    assert resp.status_code == 400
    assert "počet" in resp.get_json()["error"]


def test_discarding_then_finding_clears_the_discard(client, box):
    """One person marks it gone, another finds it and puts it somewhere."""
    item = make_item(client, box["id"], count=4, unit="ks")
    check = start_check(client)
    observe(client, box["id"], item["id"], discarded=True)
    observe(client, box["id"], item["id"], discarded=False)
    complete(client, check)
    after = get_item(client, item["id"])
    assert after["discarded_at"] is None
    assert (after["count"], after["unit"]) == (4, "ks")   # re-seeded from the item


def test_a_check_revives_a_discarded_item_by_observation_only(client, box):
    """Finding a retired thing during a check ("Přesunout sem" on it) is an observation:
    the master row stays retired until completion, so nobody else's observation gets
    overwritten, and the discarded shelf says which box it is heading for."""
    item = make_item(client, box["id"])
    discard(client, item)
    check = start_check(client)
    assert observe(client, box["id"], item["id"],
                   discarded=False, box_id=box["id"]).status_code == 200

    still = get_item(client, item["id"])
    assert still["discarded_at"] is not None and still["box_id"] is None
    overview = page_data(client, "/inventory")
    assert overview["revived"] == {str(item["id"]): box["id"]}
    assert [i["id"] for i in overview["discarded"]] == [item["id"]]

    complete(client, check)
    back = get_item(client, item["id"])
    assert back["discarded_at"] is None and back["box_id"] == box["id"]
    assert page_data(client, "/inventory")["revived"] == {}


def test_found_item_must_have_a_box(client, box):
    item = make_item(client, box["id"])
    discard(client, item)
    start_check(client)
    resp = observe(client, box["id"], item["id"])   # no box to put it in
    assert resp.status_code == 400
    assert "krabici" in resp.get_json()["error"]
    # The rejected observation must not linger half-created: the item stays unchecked.
    assert box_state(client, box["id"])["records"] == []


def test_move_propagates_and_shifts_the_progress(client, box):
    target = make_box(client, "Krabice 2")
    item = make_item(client, box["id"])
    check = start_check(client)
    state = observe(client, box["id"], item["id"], box_id=target["id"]).get_json()["state"]
    # Moved away: the source box neither counts it as done nor as work left.
    assert (state["checked"], state["total"]) == (0, 0)
    assert state["items"][0]["id"] == item["id"]        # still listed, as "moved away"
    target_state = box_state(client, target["id"])
    assert (target_state["checked"], target_state["total"]) == (1, 1)

    complete(client, check)
    assert get_item(client, item["id"])["box_id"] == target["id"]


def test_note_is_appended_to_the_item(client, box):
    item = make_item(client, box["id"], note="Koupeno 2024")
    check = start_check(client, name="Inventura po LŠMF")
    observe(client, box["id"], item["id"], note="3 rozbité")
    complete(client, check)
    assert get_item(client, item["id"])["note"] == "Koupeno 2024\nInventura po LŠMF: 3 rozbité"


def test_deleting_the_observation_returns_the_item_to_unchecked(client, box):
    item = make_item(client, box["id"], count=4)
    check = start_check(client)
    observe(client, box["id"], item["id"], count=1)
    state = client.delete(f"/api/inventory/boxes/{box['id']}/records/{item['id']}",
                          headers=ADMIN).get_json()["state"]
    assert state["records"] == [] and (state["checked"], state["total"]) == (0, 1)
    complete(client, check)
    assert get_item(client, item["id"])["count"] == 4


def test_new_item_during_a_check_counts_as_observed(client, box):
    """A thing found mid-check is created with its count and at once observed with it;
    the count lives on the item, so cancelling keeps it and completing changes nothing."""
    check = start_check(client)
    item = make_item(client, box["id"], name="Nově nalezeno", count=3, unit="ks")
    state = box_state(client, box["id"])
    record = state["records"][0]
    assert (record["item_id"], record["count"], record["unit"], record["discarded"]) \
        == (item["id"], 3, "ks", False)
    assert (state["checked"], state["total"]) == (1, 1)
    client.delete(f"/api/inventory/checks/{check['id']}", headers=ADMIN)
    kept = get_item(client, item["id"])
    assert (kept["count"], kept["unit"]) == (3, "ks")

    check = start_check(client)
    found = make_item(client, box["id"], name="Další nález", count=2)
    summary = complete(client, check).get_json()["check"]["summary"]
    assert (summary["checked"], summary["adjusted"]) == (1, 0)
    assert get_item(client, found["id"])["count"] == 2


def test_observation_needs_a_running_check(client, box):
    item = make_item(client, box["id"])
    resp = observe(client, box["id"], item["id"], count=1)
    assert resp.status_code == 400
    assert "Neprobíhá" in resp.get_json()["error"]


def test_summary_counts_what_happened_and_the_preview_agrees(client, box):
    """The confirm dialog shows the preview; it must not drift from complete_check."""
    target = make_box(client, "Krabice 2")
    kept = make_item(client, box["id"], name="Beze změny", count=1)
    adjusted = make_item(client, box["id"], name="Přepočítáno", count=1)
    reunited = make_item(client, box["id"], name="Jiná jednotka", count=1, unit="ks")
    gone = make_item(client, box["id"], name="Zmizelo")
    long_gone = make_item(client, box["id"], name="Dávno pryč")
    moved = make_item(client, box["id"], name="Přesunuto")
    revived = make_item(client, box["id"], name="Vráceno")
    discard(client, revived)
    retired_on = discard(client, long_gone).get_json()["item"]["discarded_at"]
    check = start_check(client)
    observe(client, box["id"], kept["id"])
    observe(client, box["id"], adjusted["id"], count=7)
    observe(client, box["id"], reunited["id"], unit="m")     # a unit change is an adjustment
    observe(client, box["id"], gone["id"], discarded=True)
    observe(client, box["id"], long_gone["id"], discarded=True)   # already retired: no change
    observe(client, box["id"], moved["id"], box_id=target["id"])
    # A revive lands in a box too, but must count only as revived, not as moved.
    observe(client, box["id"], revived["id"], box_id=box["id"])

    preview = client.get(f"/api/inventory/checks/{check['id']}/preview",
                         headers=ADMIN).get_json()["summary"]
    summary = complete(client, check).get_json()["check"]["summary"]
    assert preview == summary
    assert summary == {"checked": 7, "adjusted": 2, "discarded": 1, "moved": 1, "revived": 1}
    assert get_item(client, long_gone["id"])["discarded_at"] == retired_on
    assert client.get(f"/api/inventory/checks/{check['id']}/preview",
                      headers=ADMIN).status_code == 400   # finished = nothing to preview


# --- a master edit during a check invalidates the observation -----------------

def test_the_amount_cannot_be_edited_from_under_a_running_check(client, box):
    """The pages lock the amount fields while a check runs, and this is what makes that
    true for an API client too: otherwise a patch would silently delete somebody's
    observation (or be reverted by it at completion)."""
    item = make_item(client, box["id"], count=5, unit="ks")
    check = start_check(client)
    observe(client, box["id"], item["id"], count=5, unit="ks")

    refused = client.patch(f"/api/inventory/items/{item['id']}", json={"count": 3},
                           headers=ADMIN)
    assert refused.status_code == 400
    assert "inventura" in refused.get_json()["error"]
    assert get_item(client, item["id"])["count"] == 5           # nothing was written
    assert len(box_state(client, box["id"])["records"]) == 1    # and nothing was dropped

    # The other fields stay editable, and repeating the same amount is not a change.
    assert client.patch(f"/api/inventory/items/{item['id']}", json={"note": "police 2"},
                        headers=ADMIN).status_code == 200
    assert client.patch(f"/api/inventory/items/{item['id']}", json={"count": 5, "unit": "ks"},
                        headers=ADMIN).status_code == 200
    complete(client, check)
    assert get_item(client, item["id"])["count"] == 5


def test_the_amount_is_editable_once_no_check_runs(client, box):
    item = make_item(client, box["id"], count=5)
    check = start_check(client)
    client.delete(f"/api/inventory/checks/{check['id']}", headers=ADMIN)
    assert client.patch(f"/api/inventory/items/{item['id']}", json={"count": 3},
                        headers=ADMIN).status_code == 200
    assert get_item(client, item["id"])["count"] == 3


def test_moving_an_item_from_its_detail_drops_the_observation(client, box):
    target = make_box(client, "Krabice 2")
    item = make_item(client, box["id"])
    check = start_check(client)
    observe(client, box["id"], item["id"])            # observed in the old box
    client.patch(f"/api/inventory/items/{item['id']}",
                 json={"box_id": target["id"]}, headers=ADMIN)
    complete(client, check)
    # The stale observation would have moved it back into the source box.
    assert get_item(client, item["id"])["box_id"] == target["id"]


def test_discarding_from_the_detail_drops_the_observation(client, box):
    item = make_item(client, box["id"])
    check = start_check(client)
    observe(client, box["id"], item["id"])            # "it is here and fine"
    discard(client, item)
    complete(client, check)
    # The stale observation would have revived it.
    assert get_item(client, item["id"])["discarded_at"] is not None


# --- completion: races and records that lost their box ------------------------

def test_cancel_cannot_delete_a_check_completed_meanwhile(client, box):
    """The status is checked in SQL, not only on the row this request happens to hold:
    a cancel racing a completion would otherwise erase the finished check and its
    observations while the item writes stayed."""
    item = make_item(client, box["id"], count=5)
    check = start_check(client)
    observe(client, box["id"], item["id"], count=3)
    complete(client, check)

    row = db_session.get(InventoryCheck, check["id"])
    # What a request that loaded the check before the completion holds. Set without
    # dirtying the row, or the guard's own flush would write it back as active.
    set_committed_value(row, "active_lock", 1)
    with pytest.raises(errors.Conflict):
        inventory.cancel_check(row)
    db_session.rollback()
    assert db_session.get(InventoryCheck, check["id"]) is not None
    assert get_item(client, item["id"])["count"] == 3


def lose_the_box(check, item):
    """What deleting the record's box does to it: box_id SET NULL, from_box_id kept."""
    record = db_session.get(InventoryCheckRecord, {"check_id": check["id"], "item_id": item["id"]})
    record.box_id = None
    db_session.commit()


def test_completion_does_not_revive_a_thing_into_no_box(client, box):
    """A revive record whose box was deleted mid-check carries no position: reviving on
    it would leave a live thing in no box, invisible on every page of the warehouse."""
    item = make_item(client, box["id"])
    discard(client, item)
    check = start_check(client)
    observe(client, box["id"], item["id"], discarded=False, box_id=box["id"])
    lose_the_box(check, item)
    assert complete(client, check).status_code == 200

    fresh = get_item(client, item["id"])
    assert fresh["discarded_at"] is not None       # still retired, not half-revived
    assert fresh["box_id"] is None
    # And the counters do not claim a revive the write did not make.
    assert page_data(client, "/inventory/checks")["checks"][0]["summary"]["revived"] == 0


def test_completion_leaves_an_item_where_it_is_when_the_record_lost_its_box(client, box):
    """A record's box_id is SET NULL when that box goes: completing must not then file the
    thing nowhere, which would hide it from every page in the warehouse."""
    item = make_item(client, box["id"])
    check = start_check(client)
    observe(client, box["id"], item["id"], count=2)
    lose_the_box(check, item)
    preview = client.get(f"/api/inventory/checks/{check['id']}/preview", headers=ADMIN)
    assert preview.get_json()["summary"]["moved"] == 0
    complete(client, check)

    fresh = get_item(client, item["id"])
    assert fresh["box_id"] == box["id"]            # still on a shelf
    assert fresh["discarded_at"] is None
    assert fresh["count"] == 2                     # the amount it observed still lands


def test_completion_audits_boxes_by_their_new_name(client, box):
    """The audit row is the one place a box name outlives the box, so a completed move
    has to name where the thing went, not where the item row still said it was. Read
    from the table: the only audit endpoint is a camp's, and the warehouse has none."""
    other = make_box(client, "Krabice 2")
    moved = make_item(client, box["id"], "Přesunutá")
    gone = make_item(client, box["id"], "Ztracená")
    revived = make_item(client, box["id"], "Nalezená")
    discard(client, revived)
    check = start_check(client)
    observe(client, other["id"], moved["id"], box_id=other["id"])
    observe(client, box["id"], gone["id"], discarded=True)
    observe(client, other["id"], revived["id"], discarded=False, box_id=other["id"])
    assert complete(client, check).status_code == 200

    rows = db_session.scalars(db.select(AuditLog).where(
        AuditLog.entity_type == EntityType.inventory_item,
        AuditLog.action == AuditAction.update)).all()
    boxes = {r.entity_id: r.changes["box"] for r in rows if "box" in (r.changes or {})}
    assert boxes[moved["id"]] == ["Krabice 1", "Krabice 2"]
    assert boxes[gone["id"]] == ["Krabice 1", None]
    assert boxes[revived["id"]] == [None, "Krabice 2"]


# --- photos ------------------------------------------------------------------

def photo_url(photo, variant="thumb") -> str:
    return f"/inventory/photos/{variant}/{photo['filename']}"


@pytest.fixture
def media_dir(app, tmp_path):
    """Photos switched on, storing into a scratch directory."""
    app.extensions["camp_planner"]["media_dir"] = str(tmp_path)
    return tmp_path


def test_oversized_upload_is_a_clean_413(client, box):
    """The size gate answers before the body is read, in words, with the limit."""
    item = make_item(client, box["id"])
    resp = client.post(f"/api/inventory/items/{item['id']}/photos", headers=ADMIN,
                       environ_overrides={"CONTENT_LENGTH": str(MAX_UPLOAD_BYTES + 1)})
    assert resp.status_code == 413
    assert "příliš velká" in resp.get_json()["error"]


def test_a_chunked_body_is_capped_too(client, box, monkeypatch):
    """No declared length to refuse up front: the cap stops the body while it is read."""
    monkeypatch.setattr("camp_planner.integration.MAX_UPLOAD_BYTES", 1024)
    item = make_item(client, box["id"])
    resp = client.post(f"/api/inventory/items/{item['id']}/photos", headers=ADMIN,
                       input_stream=io.BytesIO(b"x" * 4096),
                       content_type="multipart/form-data; boundary=x",
                       environ_overrides={"CONTENT_LENGTH": "", "wsgi.input_terminated": True})
    assert resp.status_code == 413


def test_upload_is_refused_without_media_dir(client, app, box):
    app.extensions["camp_planner"]["media_dir"] = None   # whatever the developer's env says
    item = make_item(client, box["id"])
    resp = upload(client, item["id"], b"x")
    assert resp.status_code == 400
    assert "není" in resp.get_json()["error"]


def test_photo_is_stored_resized_and_served(client, app, box, media_dir):
    from PIL import Image

    item = make_item(client, box["id"])
    resp = upload(client, item["id"], png((4000, 3000)))
    assert resp.status_code == 200, resp.get_json()
    photo = resp.get_json()["item"]["photos"][0]

    stored = sorted((media_dir / "inventory").rglob("*.jpg"))
    assert len(stored) == len(media.SIZES)
    assert max(Image.open(p).size[0] for p in stored) == media.FULL_PX   # the original is not kept

    served = client.get(photo_url(photo), headers=ADMIN)
    assert served.status_code == 200
    assert served.cache_control.private
    assert client.get(photo_url(photo, "mini"), headers=ADMIN).status_code == 200
    assert client.get(photo_url(photo, "full")).status_code == 401   # not public

    # Anything but our own names in one of our sizes is a probe, and a missing file is
    # a plain 404 too.
    for url in (photo_url(photo, "orig"), "/inventory/photos/thumb/etc.jpg",
                "/inventory/photos/thumb/" + "a" * 32 + ".jpg"):
        assert client.get(url, headers=ADMIN).status_code == 404, url
    app.extensions["camp_planner"]["media_dir"] = None
    assert client.get(photo_url(photo), headers=ADMIN).status_code == 404


def test_relative_media_dir_serves_what_it_stored(client, app, box, tmp_path, monkeypatch):
    """A relative MEDIA_DIR must mean the same directory for writing and serving
    (send_from_directory would otherwise resolve it against the package, not cwd)."""
    monkeypatch.chdir(tmp_path)
    app.extensions["camp_planner"]["media_dir"] = "media"
    item = make_item(client, box["id"])
    photo = upload(client, item["id"]).get_json()["item"]["photos"][0]
    assert (tmp_path / "media" / "inventory").is_dir()
    assert client.get(photo_url(photo), headers=ADMIN).status_code == 200


def test_non_image_upload_fails_as_a_business_error(client, box, media_dir):
    item = make_item(client, box["id"])
    resp = upload(client, item["id"], b"nejsem obrazek")
    assert resp.status_code == 400
    assert not list((media_dir / "inventory").rglob("*.jpg"))   # no orphan files


def test_a_pixel_bomb_is_a_business_error_too(client, box, media_dir, monkeypatch):
    """A few MB of JPEG can claim hundreds of megapixels; Pillow's guard raises an error
    that is neither OSError nor UnidentifiedImageError, so it needs answering as a 400."""
    from PIL import Image

    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 16)   # any real image trips the guard
    item = make_item(client, box["id"])
    resp = upload(client, item["id"])
    assert resp.status_code == 400
    assert "rozměry" in resp.get_json()["error"]


def test_only_photo_formats_are_decoded(client, box, media_dir):
    """EPS would run Ghostscript on the upload; Pillow must not even try it."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, "EPS")
    item = make_item(client, box["id"])
    resp = upload(client, item["id"], buffer.getvalue())
    assert resp.status_code == 400
    assert "není obrázek" in resp.get_json()["error"]   # refused unread, not a failed decode
    assert not list((media_dir / "inventory").rglob("*.jpg"))


def test_a_small_file_claiming_many_pixels_is_refused(client, box, media_dir):
    """Below Pillow's own guard, and a PNG does not shrink while decoding."""
    item = make_item(client, box["id"])
    resp = upload(client, item["id"], png((8000, 6000)))
    assert resp.status_code == 400
    assert "rozměry" in resp.get_json()["error"]


def test_a_failed_variant_leaves_no_orphan_files(media_dir, monkeypatch):
    """store() hands back the name only once every size exists, so nobody else can clean
    up a half-written set: it has to do it itself."""
    from PIL import Image

    real_save, calls = Image.Image.save, []

    def flaky(self, fp, *args, **kwargs):
        calls.append(fp)
        if len(calls) == len(media.SIZES):   # the last size fails, the others are on disk
            raise OSError("disk full")
        return real_save(self, fp, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", flaky)
    with pytest.raises(OSError):
        media.store(io.BytesIO(png()))
    assert not list((media_dir / "inventory").rglob("*.jpg"))


def test_title_photo_pulls_to_front_and_delete_removes_files(client, box, media_dir):
    """photos[0] is the title everywhere; promoting reorders, the rest keep their order."""
    item = make_item(client, box["id"])
    for _ in range(3):
        resp = upload(client, item["id"])
    a, b, c = [p["id"] for p in resp.get_json()["item"]["photos"]]

    promoted = client.post(f"/api/inventory/photos/{c}/title", headers=ADMIN)
    assert [p["id"] for p in promoted.get_json()["item"]["photos"]] == [c, a, b]

    deleted = client.delete(f"/api/inventory/photos/{a}", headers=ADMIN)
    assert [p["id"] for p in deleted.get_json()["item"]["photos"]] == [c, b]
    assert len(list((media_dir / "inventory").rglob("*.jpg"))) == 2 * len(media.SIZES)


def test_deleting_an_item_deletes_its_photo_files(client, box, media_dir):
    item = make_item(client, box["id"])
    upload(client, item["id"])
    client.delete(f"/api/inventory/items/{item['id']}", headers=ADMIN)
    assert not list((media_dir / "inventory").rglob("*.jpg"))


# --- pages -------------------------------------------------------------------

def test_the_landing_header_is_the_way_into_the_warehouse(client, seeded):
    """A section link beside the heading, for a reader who may see the warehouse."""
    html = client.get("/", headers=ADMIN).get_data(as_text=True)
    head = html[html.index('class="cp-header"'):html.index("</header>")]
    assert 'href="/inventory"' in head
    assert "/inventory" not in client.get("/").get_data(as_text=True)   # anonymous: no way in


def test_pages_render_with_the_data_their_scripts_read(client, box):
    """The page JSON is the contract between the service and the page scripts; a renamed
    key would otherwise only show up as a blank page in the warehouse."""
    item = make_item(client, box["id"])
    check = start_check(client)
    observe(client, box["id"], item["id"])
    complete(client, check)

    shared = {"may_edit", "photos_enabled", "urls"}
    contract = {
        "/inventory": shared | {"boxes", "discarded", "revived", "active_check"},
        f"/inventory/boxes/{box['id']}":
            shared | {"state", "boxes", "all_items", "in_history", "has_history",
                      "delete_blocked"},
        "/inventory/checks": shared | {"active_check", "progress", "checks"},
        f"/inventory/checks/{check['id']}": shared | {"check", "groups", "boxes"},
    }
    for url, keys in contract.items():
        data = page_data(client, url)
        assert keys <= set(data), f"{url} is missing {keys - set(data)}"

    # The box state is also the page's refresh channel, so the check travels inside it.
    state = page_data(client, f"/inventory/boxes/{box['id']}")["state"]
    assert {"box", "items", "records", "checked", "total", "active_check"} <= set(state)
    group = page_data(client, f"/inventory/checks/{check['id']}")["groups"][0]
    assert {"box", "records", "names"} <= set(group)
    assert group["names"][str(item["id"])] == item["name"]
    # Every url template carries its 0 sentinel where cpDom.withId looks for it: "/0" at
    # the end or before a slash. Elsewhere ("/items/0photos") it would keep calling item 0.
    urls = page_data(client, "/inventory")["urls"]
    assert urls["photo"] is None
    templates = [u for u in urls.values() if u and "0" in u]
    assert templates
    for url in templates:
        assert re.search(r"/0(/|$)", url), url
    assert urls["record"].count("/0") == 2      # the observation url names box and item

