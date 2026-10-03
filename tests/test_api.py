"""End-to-end tests for the JSON API blueprint (mounted at /api).

Exercises the request → service → response path for each resource, the optimistic
lock, and the permission envelope (anonymous 401 / viewer 403 / editor allowed).
"""

from __future__ import annotations

import pytest
from sqlalchemy import event

from camp_planner.extensions import db
from camp_planner.schemas import ConflictOut
from tests.conftest import (
    ADMIN,
    audit,
    editor,
    get_json,
    make_activity,
    make_camp,
    make_material,
    make_slot,
    ok,
    viewer,
)

_NEW_CAMP = {
    "name": "Letní tábor", "start_date": "2026-08-01", "length_days": 5,
    "timezone": "Europe/Prague", "window_start_min": 240, "snap_minutes": 15,
}


def _count_queries(fn) -> int:
    statements: list[str] = []

    def listener(conn, cursor, statement, *_, **__):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", listener)
    try:
        fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", listener)
    return len(statements)


def _delete_slot(client, slug, slot_id):
    rev = get_json(client, f"/api/camps/{slug}/timeline")["camp"]["rev"]
    ok(client.patch(f"/api/camps/{slug}/timeline", json={"rev": rev, "deletes": [slot_id]},
                    headers=ADMIN))


# --- timeline ----------------------------------------------------------------

def test_timeline_save_conflict_on_stale_rev(client, seeded):
    url = f"/api/camps/{seeded['slug']}/timeline"
    resp = client.patch(url, json={"rev": 999, "moves": []}, headers=ADMIN)
    assert resp.status_code == 409
    body = resp.get_json()
    assert not body["ok"] and "timeline" in body and body["rev"] == 0
    # The errorhandler's body skips spectree's response validation.
    ConflictOut.model_validate(body)


def test_timeline_batch_create_move_delete(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    url = f"/api/camps/{slug}/timeline"
    assert get_json(client, url)["segments"] == []
    s1 = make_slot(client, slug, aid)
    rev = get_json(client, url)["camp"]["rev"]

    body = ok(client.patch(url, json={
        "rev": rev,
        "creates": [{"activity_id": aid, "role": "prep",
                     "start_at": "2026-07-04T13:30", "end_at": "2026-07-04T14:00"}],
        "moves": [{"slot_id": s1, "start_at": "2026-07-04T15:00", "end_at": "2026-07-04T17:00"}],
    }, headers=ADMIN))
    assert body["rev"] == rev + 1
    assert len(body["created"]) == 1 and body["created"][0]["role"] == "prep"
    tl = get_json(client, url)
    assert len(tl["segments"]) == 2 and tl["camp"]["rev"] == rev + 1

    # one batch-level summary, plus per-slot rows under the activity carrying the diff
    kinds = {(e["entity_type"], e["action"]) for e in audit(client, slug)}
    assert {("timeline", "update"), ("slot", "create"), ("slot", "update")} <= kinds
    slot_rows = audit(client, slug, entity_type="slot")
    assert all(e["activity_id"] == aid for e in slot_rows)
    move = next(e for e in slot_rows if e["action"] == "update")
    assert move["changes"]["start_at"] == ["2026-07-04T14:00:00", "2026-07-04T15:00:00"]

    ok(client.patch(url, json={"rev": body["rev"], "deletes": [s1, body["created"][0]["id"]]},
                    headers=ADMIN))
    assert get_json(client, url)["segments"] == []


def test_timeline_retype_changes_role_and_audits(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    url = f"/api/camps/{slug}/timeline"
    s1 = make_slot(client, slug, aid, role="main")

    def retype(rev):
        body = {"rev": rev, "retypes": [{"slot_id": s1, "role": "cleanup"}]}
        return ok(client.patch(url, json=body, headers=ADMIN))

    def role_diffs():
        return [e["changes"]["role"] for e in audit(client, slug, entity_type="slot")
                if e["action"] == "update" and "role" in e["changes"]]

    rev = get_json(client, url)["camp"]["rev"]
    assert retype(rev)["rev"] == rev + 1
    assert get_json(client, url)["segments"][0]["role"] == "cleanup"
    assert role_diffs() == [["main", "cleanup"]]

    retype(rev + 1)   # the same role again changes nothing and writes no audit row
    assert role_diffs() == [["main", "cleanup"]]


def test_timeline_create_rejects_foreign_activity(client, seeded):
    resp = client.patch(f"/api/camps/{seeded['slug']}/timeline", json={
        "rev": 0,
        "creates": [{"activity_id": 99999, "start_at": "2026-07-04T13:00", "end_at": "2026-07-04T14:00"}],
    }, headers=ADMIN)
    assert resp.status_code == 400


def test_timeline_create_rejects_end_before_start(client, seeded):
    resp = client.patch(f"/api/camps/{seeded['slug']}/timeline", json={
        "rev": 0,
        "creates": [{"activity_id": seeded["activity_id"],
                     "start_at": "2026-07-04T16:00", "end_at": "2026-07-04T14:00"}],
    }, headers=ADMIN)
    assert resp.status_code == 422


def test_timeline_save_requires_rev(client, seeded):
    # rev is the optimistic lock: omitting it must not silently bypass it
    resp = client.patch(f"/api/camps/{seeded['slug']}/timeline", json={"moves": []}, headers=ADMIN)
    assert resp.status_code == 422


def test_timeline_save_force_overrides_stale_rev(client, seeded):
    # the conflict dialog's deliberate "Přepsat"
    resp = client.patch(f"/api/camps/{seeded['slug']}/timeline", json={
        "rev": 999, "force": True,
        "creates": [{"activity_id": seeded["activity_id"],
                     "start_at": "2026-07-04T14:00", "end_at": "2026-07-04T15:00"}],
    }, headers=ADMIN)
    assert resp.status_code == 200


def test_timeline_save_rejects_slot_outside_camp_days(client, seeded):
    """A slot outside the camp window would persist but never render (slice_segments
    clamps it away), an invisible slot no UI could reach. Camp: 2026-07-04 + 3 days,
    window 04:00 → valid range 07-04T04:00 .. 07-07T04:00."""
    slug, aid = seeded["slug"], seeded["activity_id"]
    url = f"/api/camps/{slug}/timeline"

    def save(start, end, slot_id=None):
        rev = get_json(client, url)["camp"]["rev"]
        change = ({"moves": [{"slot_id": slot_id, "start_at": start, "end_at": end}]} if slot_id
                  else {"creates": [{"activity_id": aid, "start_at": start, "end_at": end}]})
        return client.patch(url, json={"rev": rev, **change}, headers=ADMIN)

    resp = save("2026-07-08T10:00", "2026-07-08T12:00")
    assert resp.status_code == 400 and "mimo dny akce" in resp.get_json()["error"]
    assert save("2026-07-04T02:00", "2026-07-04T03:30").status_code == 400

    sid = make_slot(client, slug, aid)
    assert save("2026-07-07T10:00", "2026-07-07T11:00", slot_id=sid).status_code == 400

    # boundaries are inclusive: a night program running to the last window end is fine
    assert save("2026-07-06T22:00", "2026-07-07T04:00").status_code == 200


def test_timeline_span_seconds_are_dropped(client, seeded):
    # sub-minute times would render truncated and never round-trip
    created = ok(client.patch(f"/api/camps/{seeded['slug']}/timeline", json={
        "rev": 0,
        "creates": [{"activity_id": seeded["activity_id"],
                     "start_at": "2026-07-04T14:00:30", "end_at": "2026-07-04T16:00:45"}],
    }, headers=ADMIN))["created"][0]
    assert created["start_at"] == "2026-07-04T14:00:00"
    assert created["end_at"] == "2026-07-04T16:00:00"


def test_timeline_query_count_is_flat(client, seeded):
    """Read and save are eager-loaded (loaders.TIMELINE): their query count must not grow
    with the number of activities (N+1 guard)."""
    slug, org_id = seeded["slug"], seeded["org_id"]
    url = f"/api/camps/{slug}/timeline"

    def wire(activity_id):   # a garant, and a slot with an attendee
        ok(client.put(f"/api/activities/{activity_id}/orgs",
                      json={"orgs": [{"org_id": org_id, "role": "garant"}]}, headers=ADMIN))
        sid = make_slot(client, slug, activity_id)
        ok(client.patch(f"/api/slots/{sid}", json={"org_ids": [org_id]}, headers=ADMIN))
        return sid

    def read_and_save(hour):
        read = _count_queries(lambda: ok(client.get(url, headers=ADMIN)))
        body = {"rev": get_json(client, url)["camp"]["rev"], "moves": [
            {"slot_id": moved, "start_at": f"2026-07-04T{hour}:00", "end_at": f"2026-07-04T{hour}:30"}]}
        save = _count_queries(lambda: ok(client.patch(url, json=body, headers=ADMIN)))
        return read, save

    moved = wire(seeded["activity_id"])
    wire(make_activity(client, slug, "B"))
    two = read_and_save("12")
    for i in range(3):
        wire(make_activity(client, slug, f"C{i}"))
    assert read_and_save("13") == two


# --- activities --------------------------------------------------------------

@pytest.mark.parametrize("url, body, field", [
    ("/api/camps/t/activities", {}, "title"),
    ("/api/activities/{aid}/todos", {"title": ""}, "title"),
    ("/api/camps", {**_NEW_CAMP, "name": ""}, "name"),
])
def test_schema_errors_are_422_with_the_pydantic_list(client, seeded, url, body, field):
    resp = client.post(url.format(aid=seeded["activity_id"]), json=body, headers=ADMIN)
    assert resp.status_code == 422
    assert any(field in e["loc"] for e in resp.get_json())


def test_patch_explicit_null_on_required_field_is_400(client, seeded):
    """Patch schemas type required fields as `X | None` ("absent = unchanged"), so an
    explicit null passes validation; apply_patch must reject it (400) before it hits the
    NOT NULL column (500)."""
    slug, aid = seeded["slug"], seeded["activity_id"]

    resp = client.patch(f"/api/activities/{aid}", json={"title": None}, headers=ADMIN)
    assert resp.status_code == 400 and "title" in resp.get_json()["error"]
    assert client.patch(f"/api/activities/{aid}", json={"type": None}, headers=ADMIN).status_code == 400

    resp = client.post(f"/api/activities/{aid}/todos", json={"title": "Úkol"}, headers=ADMIN)
    tid = ok(resp)["todo"]["id"]
    assert client.patch(f"/api/todos/{tid}", json={"title": None}, headers=ADMIN).status_code == 400

    mid = make_material(client, slug, "Papír")["id"]
    resp = client.patch(f"/api/camps/{slug}/materials/{mid}", json={"name": None}, headers=ADMIN)
    assert resp.status_code == 400

    # a null on a nullable column still means "clear"
    resp = client.patch(f"/api/activities/{aid}", json={"description_md": None}, headers=ADMIN)
    assert resp.status_code == 200


def test_activity_list(client, seeded):
    slug = seeded["slug"]
    make_activity(client, slug, "Druhá hra")
    activities = get_json(client, f"/api/camps/{slug}/activities")["activities"]
    assert seeded["activity_id"] in [a["id"] for a in activities]
    assert "Druhá hra" in [a["title"] for a in activities]
    # the full ActivityOut shape per item
    assert "slots" in activities[0] and "material_needs" in activities[0]


@pytest.mark.parametrize("method, url, body", [
    ("put", "/api/activities/{aid}/orgs", {"orgs": [{"org_id": 99999, "role": "garant"}]}),
    ("post", "/api/activities/{aid}/todos", {"title": "X", "org_ids": [99999]}),
    ("patch", "/api/slots/{sid}", {"org_ids": [99999]}),
    ("patch", "/api/camps/t/materials/{mid}", {"org_ids": [99999]}),
])
def test_unknown_org_ids_are_400(client, seeded, method, url, body):
    aid = seeded["activity_id"]
    ids = {"aid": aid, "sid": make_slot(client, "t", aid), "mid": make_material(client, "t")["id"]}
    resp = client.open(url.format(**ids), method=method, json=body, headers=ADMIN)
    assert resp.status_code == 400 and "org" in resp.get_json()["error"].lower()


def test_duplicate_ids_are_422(client, seeded):
    aid, oid, tid = seeded["activity_id"], seeded["org_id"], seeded["tag_id"]
    garant = {"org_id": oid, "role": "garant"}
    for method, url, body in [
        ("put", f"/api/activities/{aid}/orgs", {"orgs": [garant, garant]}),
        ("put", f"/api/activities/{aid}/tags", {"tags": [{"tag_id": tid}, {"tag_id": tid}]}),
        ("post", f"/api/activities/{aid}/todos", {"title": "X", "org_ids": [oid, oid]}),
    ]:
        assert client.open(url, method=method, json=body, headers=ADMIN).status_code == 422, url


# --- assignments + tags ------------------------------------------------------

def test_set_orgs_and_tags(client, seeded):
    aid = seeded["activity_id"]
    resp = client.put(f"/api/activities/{aid}/orgs",
                      json={"orgs": [{"org_id": seeded["org_id"], "role": "garant"}]}, headers=ADMIN)
    assert ok(resp)["orgs"][0]["initials"] == "K"

    resp = client.put(f"/api/activities/{aid}/tags",
                      json={"tags": [{"tag_id": seeded["tag_id"], "value": "ano"}]}, headers=ADMIN)
    assert ok(resp)["tags"][0]["value"] == "ano"


def test_set_tags_records_value_diff_in_audit(client, seeded):
    slug, aid, tid = seeded["slug"], seeded["activity_id"], seeded["tag_id"]  # "Důležité", text

    def put(tags):
        ok(client.put(f"/api/activities/{aid}/tags", json={"tags": tags}, headers=ADMIN))
        return [e["changes"] for e in audit(client, slug, entity_type="tag") if e["changes"]]

    assert put([{"tag_id": tid, "value": "ano"}])[0] == {"Důležité": [None, "ano"]}
    assert put([{"tag_id": tid, "value": "ne"}])[0] == {"Důležité": ["ano", "ne"]}
    assert put([])[0] == {"Důležité": ["ne", None]}
    assert len(put([])) == 3   # the no-op writes nothing


def test_tag_value_follows_the_tag_kind(client, seeded):
    aid, text = seeded["activity_id"], seeded["tag_id"]
    url = f"/api/activities/{aid}/tags"
    # a camp tag not applied to the activity has no value to set
    assert client.patch(f"{url}/{text}", json={"value": "x"}, headers=ADMIN).status_code == 404

    saved = ok(client.put("/api/camps/t/tags", json={"items": [
        {"id": text, "name": "Důležité", "kind": "text"},
        {"name": "Postup", "kind": "progress"}, {"name": "Štítek", "kind": "label"}]},
        headers=ADMIN))["items"]
    ids = {t["name"]: t["id"] for t in saved}
    ok(client.put(url, json={"tags": [{"tag_id": i} for i in ids.values()]}, headers=ADMIN))

    def patch(name, value):
        return client.patch(f"{url}/{ids[name]}", json={"value": value}, headers=ADMIN)

    assert ok(patch("Důležité", "hotovo"))["tag"]["value"] == "hotovo"
    assert patch("Postup", "200").status_code == 400
    assert ok(patch("Postup", "60"))["tag"]["value"] == "60"
    assert patch("Štítek", "x").status_code == 400


def test_activity_delete_waits_for_its_slots(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]

    def listed():
        return [a["id"] for a in get_json(client, f"/api/camps/{slug}/activities")["activities"]]

    sid = make_slot(client, slug, aid)
    resp = client.delete(f"/api/activities/{aid}", headers=ADMIN)
    assert resp.status_code == 400 and "naplánované sloty" in resp.get_json()["error"]
    assert aid in listed()

    _delete_slot(client, slug, sid)
    assert ok(client.delete(f"/api/activities/{aid}", headers=ADMIN))["id"] == aid
    assert aid not in listed()


def test_activity_merge_transfers_slots_todos_and_joins_needs(client, seeded):
    slug, src, oid = seeded["slug"], seeded["activity_id"], seeded["org_id"]
    dst = make_activity(client, slug, "Cíl")
    make_slot(client, slug, src)
    client.post(f"/api/activities/{src}/todos",
                json={"title": "Koupit lano", "org_ids": [oid]}, headers=ADMIN)
    mid = make_material(client, slug, "lano", unit="m")["id"]
    client.post(f"/api/activities/{src}/materials", json={"material_id": mid, "amount": 30}, headers=ADMIN)
    client.post(f"/api/activities/{dst}/materials", json={"material_id": mid, "amount": 20}, headers=ADMIN)

    resp = client.post(f"/api/activities/{src}/merge", json={"into": dst}, headers=ADMIN)
    assert ok(resp)["activity"]["id"] == dst

    acts = [a["id"] for a in get_json(client, f"/api/camps/{slug}/activities")["activities"]]
    assert src not in acts
    target = get_json(client, f"/api/activities/{dst}")["activity"]
    assert len(target["slots"]) == 1
    assert [t["title"] for t in target["todos"]] == ["Koupit lano"]
    assert [o["org_id"] for o in target["todos"][0]["orgs"]] == [oid]
    assert len(target["material_needs"]) == 1 and target["material_needs"][0]["amount"] == 50


def test_activity_merge_rejects_cross_camp(client, seeded):
    make_camp(client, "jina")
    dst = make_activity(client, "jina", "Jiná")
    resp = client.post(f"/api/activities/{seeded['activity_id']}/merge", json={"into": dst}, headers=ADMIN)
    assert resp.status_code == 400 and "různých akcí" in resp.get_json()["error"]


def test_slot_orgs_set_and_audited(client, seeded):
    slug, aid, oid = seeded["slug"], seeded["activity_id"], seeded["org_id"]
    slot_id = make_slot(client, slug, aid)
    resp = client.patch(f"/api/slots/{slot_id}", json={"org_ids": [oid]}, headers=ADMIN)
    assert [o["initials"] for o in ok(resp)["orgs"]] == ["K"]

    def org_audits():
        return [e for e in audit(client, slug, entity_type="slot")
                if e["changes"] and "orgs" in e["changes"]]

    changes = org_audits()
    assert len(changes) == 1
    assert changes[0]["activity_id"] == aid and changes[0]["changes"]["orgs"] == [[], ["K"]]

    client.patch(f"/api/slots/{slot_id}", json={"org_ids": [oid]}, headers=ADMIN)   # no-op
    assert len(org_audits()) == 1


def test_slot_override_name_set_clear_and_combined(client, seeded):
    slug, aid, oid = seeded["slug"], seeded["activity_id"], seeded["org_id"]
    slot_id = make_slot(client, slug, aid)

    def patch(**body):
        out = ok(client.patch(f"/api/slots/{slot_id}", json=body, headers=ADMIN))
        return out["override_name"], [o["initials"] for o in out["orgs"]]

    assert patch(override_name="Ranní rozcvička") == ("Ranní rozcvička", [])
    seg = next(s for s in get_json(client, f"/api/camps/{slug}/timeline")["segments"]
               if s["slot_id"] == slot_id)
    assert seg["override_name"] == "Ranní rozcvička"

    assert patch(org_ids=[oid], override_name="Hra") == ("Hra", ["K"])
    # a null org_ids means "unchanged" like an omitted one; only [] clears
    assert patch(org_ids=None, override_name="Jiná") == ("Jiná", ["K"])
    # a whitespace-only override clears it (falls back to the activity title)
    assert patch(override_name="  ") == (None, ["K"])
    assert patch(org_ids=[]) == (None, [])

    name_changes = [e["changes"]["override_name"] for e in audit(client, slug, entity_type="slot")
                    if e["changes"] and "override_name" in e["changes"]]
    assert [None, "Ranní rozcvička"] in name_changes and ["Jiná", None] in name_changes


# --- todos -------------------------------------------------------------------

def test_todo_lifecycle(client, seeded):
    aid = seeded["activity_id"]
    resp = client.post(f"/api/activities/{aid}/todos", json={"title": "Koupit lano"}, headers=ADMIN)
    todo_id = ok(resp)["todo"]["id"]

    resp = client.patch(f"/api/todos/{todo_id}", json={"is_done": True}, headers=ADMIN)
    assert ok(resp)["todo"]["is_done"] is True

    assert ok(client.delete(f"/api/todos/{todo_id}", headers=ADMIN))["id"] == todo_id
    assert get_json(client, f"/api/activities/{aid}")["activity"]["todos"] == []


def test_todo_org_assignment(client, seeded):
    aid, org_id = seeded["activity_id"], seeded["org_id"]
    resp = client.post(f"/api/activities/{aid}/todos",
                       json={"title": "Koupit lano", "org_ids": [org_id]}, headers=ADMIN)
    todo = ok(resp)["todo"]
    assert todo["orgs"] == [{"org_id": org_id, "initials": "K"}]

    def patch(**body):
        out = ok(client.patch(f"/api/todos/{todo['id']}", json=body, headers=ADMIN))
        return [o["org_id"] for o in out["todo"]["orgs"]]

    assert patch(org_ids=[]) == []
    patch(org_ids=[org_id])
    assert patch(is_done=True) == [org_id]   # a patch without org_ids leaves them alone


# --- materials ---------------------------------------------------------------

def test_material_catalog_create_list_and_dedup(client, seeded):
    slug = seeded["slug"]
    material = make_material(client, slug, note="bílý", url="https://shop/a4")
    assert material["name"] == "A4 papír" and material["url"] == "https://shop/a4"

    mats = get_json(client, f"/api/camps/{slug}/materials")["materials"]
    assert [m["name"] for m in mats] == ["A4 papír"] and mats[0]["note"] == "bílý"

    # a normalized-equal name can't create a second catalog row
    resp = client.post(f"/api/camps/{slug}/materials", json={"name": "papír A4"}, headers=ADMIN)
    assert resp.status_code == 400


def test_material_need_add_by_id(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    material_id = make_material(client, slug, unit="ks")["id"]
    resp = client.post(f"/api/activities/{aid}/materials",
                       json={"material_id": material_id, "amount": 10}, headers=ADMIN)
    n = ok(resp)["need"]
    assert n["amount"] == 10 and n["material"]["name"] == "A4 papír" and n["material"]["unit"] == "ks"

    # one catalog material at most once per activity
    dup = client.post(f"/api/activities/{aid}/materials", json={"material_id": material_id}, headers=ADMIN)
    assert dup.status_code == 400


@pytest.mark.parametrize("amount", [float("inf"), float("nan"), -5])
def test_material_need_amount_must_be_a_finite_number(client, seeded, amount):
    """json.dumps writes inf as the bare token Infinity, which no JSON parser reads back:
    one such need would blank every page that renders from an inlined payload."""
    slug, aid = seeded["slug"], seeded["activity_id"]
    material_id = make_material(client, slug)["id"]
    resp = client.post(f"/api/activities/{aid}/materials",
                       json={"material_id": material_id, "amount": amount}, headers=ADMIN)
    assert resp.status_code == 422

    resp = client.post(f"/api/activities/{aid}/materials",
                       json={"material_id": material_id, "amount": 3}, headers=ADMIN)
    need_id = ok(resp)["need"]["id"]
    patched = client.patch(f"/api/material-needs/{need_id}", json={"amount": amount}, headers=ADMIN)
    assert patched.status_code == 422
    assert get_json(client, f"/api/activities/{aid}")["activity"]["material_needs"][0]["amount"] == 3


def test_material_merge_migrates_usages_and_pins_unit(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    src = make_material(client, slug, "papír", unit="ks")["id"]
    dst = make_material(client, slug, "kancelářský papír", unit="balení")["id"]
    client.post(f"/api/activities/{aid}/materials", json={"material_id": src, "amount": 5}, headers=ADMIN)

    resp = client.post(f"/api/camps/{slug}/materials/{src}/merge", json={"into": dst}, headers=ADMIN)
    assert ok(resp)["material"]["id"] == dst

    mats = get_json(client, f"/api/camps/{slug}/materials")["materials"]
    assert src not in [m["id"] for m in mats]
    needs = get_json(client, f"/api/activities/{aid}")["activity"]["material_needs"]
    # the usage keeps its effective unit: the source's default, pinned as an override
    assert len(needs) == 1
    assert needs[0]["material"]["id"] == dst and needs[0]["unit"] == "ks"


def _two_materials_needed(client, slug, aid, *, src_unit="ks"):
    """Two catalog materials, both needed by the activity (50 dst + 20 src); returns (src, dst)."""
    dst = make_material(client, slug, "papíry", unit="ks")["id"]
    src = make_material(client, slug, "papíry A4", unit=src_unit)["id"]
    client.post(f"/api/activities/{aid}/materials", json={"material_id": dst, "amount": 50}, headers=ADMIN)
    client.post(f"/api/activities/{aid}/materials", json={"material_id": src, "amount": 20}, headers=ADMIN)
    return src, dst


def test_material_merge_sums_amounts_when_activity_uses_both(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    src, dst = _two_materials_needed(client, slug, aid)

    ok(client.post(f"/api/camps/{slug}/materials/{src}/merge", json={"into": dst}, headers=ADMIN))

    needs = get_json(client, f"/api/activities/{aid}")["activity"]["material_needs"]
    assert len(needs) == 1
    assert needs[0]["material"]["id"] == dst
    assert needs[0]["amount"] == 70


def test_material_delete_waits_for_its_needs(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]

    def catalog():
        return [m["id"] for m in get_json(client, f"/api/camps/{slug}/materials")["materials"]]

    mid = make_material(client, slug)["id"]
    resp = client.post(f"/api/activities/{aid}/materials",
                       json={"material_id": mid, "amount": 3}, headers=ADMIN)
    need_id = ok(resp)["need"]["id"]

    resp = client.delete(f"/api/camps/{slug}/materials/{mid}", headers=ADMIN)
    assert resp.status_code == 400 and "nelze smazat" in resp.get_json()["error"]
    assert mid in catalog()
    needs = get_json(client, f"/api/activities/{aid}")["activity"]["material_needs"]
    assert [n["material"]["id"] for n in needs] == [mid]

    ok(client.delete(f"/api/material-needs/{need_id}", headers=ADMIN))
    assert ok(client.delete(f"/api/camps/{slug}/materials/{mid}", headers=ADMIN))["id"] == mid
    assert mid not in catalog()


def test_material_merge_fails_on_unit_mismatch(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    src, dst = _two_materials_needed(client, slug, aid, src_unit="balení")

    resp = client.post(f"/api/camps/{slug}/materials/{src}/merge", json={"into": dst}, headers=ADMIN)
    assert resp.status_code == 400
    # nothing changed: both materials and both needs survive for manual fixing
    mats = [m["id"] for m in get_json(client, f"/api/camps/{slug}/materials")["materials"]]
    assert src in mats and dst in mats
    assert len(get_json(client, f"/api/activities/{aid}")["activity"]["material_needs"]) == 2


# --- camps (create + edit) ---------------------------------------------------

def test_camp_create_starts_empty(client, app):
    camp = ok(client.post("/api/camps", json=_NEW_CAMP, headers=ADMIN))["camp"]
    assert camp["slug"] == "letni-tabor" and camp["length_days"] == 5
    assert get_json(client, "/api/camps/letni-tabor")["camp"]["name"] == "Letní tábor"
    assert [c["slug"] for c in get_json(client, "/api/camps")["camps"]] == ["letni-tabor"]
    assert get_json(client, "/api/camps/letni-tabor/timeline")["categories"] == []


@pytest.mark.parametrize("copy, categories", [
    ({"copy_from": "t"}, ["hra"]),
    ({"copy_from": "t", "copy_parts": ["orgs"]}, []),
])
def test_camp_create_copies_taxonomies_from_source(client, seeded, copy, categories):
    slug = ok(client.post("/api/camps", json={**_NEW_CAMP, **copy}, headers=ADMIN))["camp"]["slug"]
    tl = get_json(client, f"/api/camps/{slug}/timeline")
    assert [c["key"] for c in tl["categories"]] == categories
    assert [o["initials"] for o in tl["orgs"]] == ["K"]


def test_camp_create_unknown_copy_source(client):
    resp = client.post("/api/camps", json={**_NEW_CAMP, "copy_from": "nope"}, headers=ADMIN)
    assert resp.status_code == 400


def test_camp_create_duplicate_slug(client, seeded):
    resp = client.post("/api/camps", json={**_NEW_CAMP, "slug": seeded["slug"]}, headers=ADMIN)
    assert resp.status_code == 400 and "Slug" in resp.get_json()["error"]


def test_camp_create_forbidden_for_editor(client, seeded):
    resp = client.post("/api/camps", json=_NEW_CAMP, headers=editor(seeded["slug"]))
    assert resp.status_code == 403


def test_camp_update_is_partial(client, seeded):
    """PATCH semantics: only the sent fields change; null is ignored for non-nullable
    settings and clears the nullable coordinates."""
    slug = seeded["slug"]
    camp = ok(client.patch(f"/api/camps/{slug}", json={"length_days": 9}, headers=ADMIN))["camp"]
    assert camp["length_days"] == 9
    assert camp["name"] == "Tábor" and camp["window_start_min"] == 240

    resp = client.patch(f"/api/camps/{slug}",
                        json={"window_start_min": None, "latitude": 50.1, "longitude": 14.4},
                        headers=ADMIN)
    assert ok(resp)["camp"]["window_start_min"] == 240
    resp = client.patch(f"/api/camps/{slug}", json={"latitude": None}, headers=ADMIN)
    assert ok(resp)["camp"]["latitude"] is None


def test_editor_cannot_change_name(client, seeded):
    slug = seeded["slug"]
    resp = client.patch(f"/api/camps/{slug}",
                        json={**_NEW_CAMP, "name": "Přejmenováno", "length_days": 4},
                        headers=editor(slug))
    camp = ok(resp)["camp"]
    assert camp["name"] == "Tábor"      # meta change ignored for editors
    assert camp["length_days"] == 4     # ...while the rest applies


# --- taxonomy ----------------------------------------------------------------

def test_taxonomy_categories_save(client, seeded):
    url = f"/api/camps/{seeded['slug']}/categories"
    items = [{"id": seeded["cat_id"], "key": "hra", "label": "Hra", "color": "#0b8043"},
             {"key": "jidlo", "label": "Jídlo", "color": "#4285f4"}]
    assert len(ok(client.put(url, json={"items": items}, headers=ADMIN))["items"]) == 2

    dup = [{"key": "x", "label": "A"}, {"key": "x", "label": "B"}]
    resp = client.put(url, json={"items": dup}, headers=ADMIN)
    assert resp.status_code == 400 and "opakuje" in resp.get_json()["error"]


def test_taxonomy_category_key_and_color_reject_injection(client, seeded):
    # key + color land unescaped in a <style> block / data-* on the timeline, so a
    # non-slug key or non-hex color must be rejected at the schema (422), not stored.
    url = f"/api/camps/{seeded['slug']}/categories"
    for bad in ({"key": "x</style>", "label": "A"}, {"label": "A", "color": "red}a{"}):
        assert client.put(url, json={"items": [bad]}, headers=ADMIN).status_code == 422


def test_taxonomy_reads_per_collection(client, seeded):
    s = seeded["slug"]
    assert {c["key"] for c in get_json(client, f"/api/camps/{s}/categories")["items"]} == {"hra"}
    assert get_json(client, f"/api/camps/{s}/orgs")["items"][0]["initials"] == "K"
    assert get_json(client, f"/api/camps/{s}/tags")["items"][0]["name"] == "Důležité"


# --- permissions -------------------------------------------------------------

def test_anonymous_is_401_and_viewer_cannot_edit(client, seeded):
    slug = seeded["slug"]
    assert client.get(f"/api/camps/{slug}/timeline").status_code == 401

    assert client.get(f"/api/camps/{slug}/timeline", headers=viewer(slug)).status_code == 200
    resp = client.post(f"/api/camps/{slug}/activities", json={"title": "X"}, headers=viewer(slug))
    assert resp.status_code == 403

    resp = client.post(f"/api/camps/{slug}/activities", json={"title": "X"}, headers=editor(slug))
    assert resp.status_code == 200


def test_unknown_activity_is_404_json(client, seeded):
    resp = client.get("/api/activities/424242", headers=ADMIN)
    assert resp.status_code == 404
    assert resp.get_json()["ok"] is False


# --- camp deletion (admin-only, must be empty) -------------------------------

def test_camp_delete_admin_only_cascades_taxonomy(client, seeded):
    # copied taxonomy and a material, but no activities: still deletes (cascade)
    make_camp(client, "empty", copy_from=seeded["slug"])
    make_material(client, "empty", "papír")
    assert client.delete("/api/camps/empty", headers=editor("empty")).status_code == 403
    assert client.delete("/api/camps/empty", headers=ADMIN).status_code == 200
    assert client.get("/api/camps/empty", headers=ADMIN).status_code == 404


def test_camp_delete_blocked_with_activities(client, seeded):
    resp = client.delete(f"/api/camps/{seeded['slug']}", headers=ADMIN)
    assert resp.status_code == 400 and "nelze smazat" in resp.get_json()["error"]


# --- catalog material update + overview --------------------------------------

def test_material_update_fields(client, seeded):
    slug = seeded["slug"]
    mid = make_material(client, slug, "papír", unit="ks")["id"]
    resp = client.patch(f"/api/camps/{slug}/materials/{mid}",
                        json={"name": "kancelářský papír", "unit": "balení", "note": "A4"}, headers=ADMIN)
    m = ok(resp)["material"]
    assert (m["name"], m["unit"], m["note"]) == ("kancelářský papír", "balení", "A4")


def test_material_acquisition_labels_and_orgs(client, seeded):
    slug, oid = seeded["slug"], seeded["org_id"]
    mid = make_material(client, slug, "lano")["id"]
    assert make_material(client, slug, "provázek")["acquisition_labels"] == []

    def patch(**body):
        resp = client.patch(f"/api/camps/{slug}/materials/{mid}", json=body, headers=ADMIN)
        return ok(resp)["material"]

    # blanks dropped, duplicates removed, order preserved
    m = patch(acquisition_labels=["půjčit: jirka", "  ", "půjčit: kačka", "půjčit: jirka"],
              org_ids=[oid])
    assert m["acquisition_labels"] == ["půjčit: jirka", "půjčit: kačka"]
    assert m["orgs"] == [{"org_id": oid, "initials": "K"}]

    # the overview serializer carries the same catalog fields (shared via _material())
    ov = next(x for x in get_json(client, f"/api/camps/{slug}/materials/overview")["materials"]
              if x["id"] == mid)
    assert ov["acquisition_labels"] == ["půjčit: jirka", "půjčit: kačka"]
    assert ov["orgs"] == [{"org_id": oid, "initials": "K"}]

    # not sent and an explicit null both leave a field unchanged; [] clears
    m = patch(org_ids=[])
    assert m["orgs"] == [] and m["acquisition_labels"] == ["půjčit: jirka", "půjčit: kačka"]
    assert patch(acquisition_labels=None)["acquisition_labels"] == ["půjčit: jirka", "půjčit: kačka"]
    assert patch(acquisition_labels=[])["acquisition_labels"] == []


def test_material_merge_carries_labels_and_orgs(client, seeded):
    slug, oid = seeded["slug"], seeded["org_id"]
    src = make_material(client, slug, "lano")["id"]
    dst = make_material(client, slug, "provaz")["id"]
    client.patch(f"/api/camps/{slug}/materials/{dst}",
                 json={"acquisition_labels": ["kup: mefisto"], "org_ids": [oid]}, headers=ADMIN)
    client.patch(f"/api/camps/{slug}/materials/{src}",
                 json={"acquisition_labels": ["kup: mefisto", "sklad: K14"], "org_ids": [oid]}, headers=ADMIN)
    resp = client.post(f"/api/camps/{slug}/materials/{src}/merge", json={"into": dst}, headers=ADMIN)
    m = ok(resp)["material"]
    assert m["id"] == dst
    assert m["acquisition_labels"] == ["kup: mefisto", "sklad: K14"]   # union, target first
    assert m["orgs"] == [{"org_id": oid, "initials": "K"}]              # not doubled


def test_material_sum_strategy(client, seeded):
    m = make_material(client, seeded["slug"], "projektor")
    assert m["sum_strategy"] == "sum"
    url = f"/api/camps/{seeded['slug']}/materials/{m['id']}"

    def patch(**body):
        return client.patch(url, json=body, headers=ADMIN)

    assert ok(patch(sum_strategy="max"))["material"]["sum_strategy"] == "max"
    assert ok(patch(unit="ks"))["material"]["sum_strategy"] == "max"
    assert patch(sum_strategy="avg").status_code == 422


def test_material_update_rename_collision(client, seeded):
    slug = seeded["slug"]
    make_material(client, slug, "papír")
    mid = make_material(client, slug, "lepidlo")["id"]
    resp = client.patch(f"/api/camps/{slug}/materials/{mid}", json={"name": "papír"}, headers=ADMIN)
    assert resp.status_code == 400 and "už v katalogu" in resp.get_json()["error"]


def test_material_overview_lists_usages(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    mid = make_material(client, slug, "papír", unit="ks")["id"]
    client.post(f"/api/activities/{aid}/materials",
                json={"material_id": mid, "amount": 30, "is_ready": True}, headers=ADMIN)
    m = next(x for x in get_json(client, f"/api/camps/{slug}/materials/overview")["materials"]
             if x["id"] == mid)
    assert len(m["usages"]) == 1
    u = m["usages"][0]
    assert u["activity_id"] == aid and u["amount"] == 30 and u["is_ready"] is True
    assert u["activity_title"] == "Akce"


# --- camp-wide TODO overview -------------------------------------------------

def test_todo_overview_lists_all_with_activity(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    client.post(f"/api/activities/{aid}/todos", json={"title": "Koupit lano"}, headers=ADMIN)
    client.post(f"/api/activities/{aid}/todos", json={"title": "Hotovo", "is_done": True}, headers=ADMIN)
    aid2 = make_activity(client, slug)
    client.post(f"/api/activities/{aid2}/todos", json={"title": "Z druhé"}, headers=ADMIN)

    todos_out = get_json(client, f"/api/camps/{slug}/todos")["todos"]
    assert len(todos_out) == 3
    titles = {t["title"]: t for t in todos_out}
    assert titles["Koupit lano"]["activity_title"] == "Akce"
    assert titles["Z druhé"]["activity_id"] == aid2
    assert titles["Hotovo"]["is_done"] is True


# --- audit log history -------------------------------------------------------

@pytest.fixture
def history(client, seeded):
    """One of each audited kind of change; the tests then only read the feed."""
    slug, aid = seeded["slug"], seeded["activity_id"]
    ok(client.patch(f"/api/activities/{aid}", json={"title": "Nová akce"}, headers=ADMIN))
    other = make_activity(client, slug)
    ok(client.post(f"/api/activities/{aid}/todos", json={"title": "Koupit lano"}, headers=ADMIN))
    src, dst = (make_material(client, slug, name)["id"] for name in ("lano A", "lano B"))
    ok(client.post(f"/api/camps/{slug}/materials/{src}/merge", json={"into": dst}, headers=ADMIN))
    ok(client.put(f"/api/camps/{slug}/categories", json={"items": [
        {"id": seeded["cat_id"], "key": "hra", "label": "Hra", "color": "#0b8043"},
        {"key": "jidlo", "label": "Jídlo", "color": "#4285f4"}]}, headers=ADMIN))
    return {**seeded, "other_id": other}


def test_audit_feed_and_activity_filter(client, history):
    slug, aid = history["slug"], history["activity_id"]
    ids = [e["id"] for e in audit(client, slug)]
    assert ids == sorted(ids, reverse=True)   # newest first (created_at has whole seconds)

    only = audit(client, slug, activity_id=aid)
    assert only and all(e["activity_id"] == aid for e in only)
    upd = next(e for e in only if e["action"] == "update")
    assert upd["changes"]["title"] == ["Akce", "Nová akce"]


def test_audit_requires_view(client, seeded):
    assert client.get(f"/api/camps/{seeded['slug']}/audit").status_code == 401


def test_audit_entity_type_filter(client, history):
    slug = history["slug"]
    mats = audit(client, slug, entity_type="material")
    assert mats and all(e["entity_type"] == "material" for e in mats)
    # an unknown filter value is a 422, not a silent empty list
    assert client.get(f"/api/camps/{slug}/audit?entity_type=bogus", headers=ADMIN).status_code == 422


def test_audit_camp_level_filter(client, history):
    entries = audit(client, history["slug"], camp_level="true")
    kinds = {(e["entity_type"], e["action"]) for e in entries}
    # the activity list, the material catalog (merges too) and the taxonomy are high-level
    assert {("activity", "create"), ("material", "create"), ("material", "merge"),
            ("category", "update")} <= kinds
    # per-activity edits belong on the activity's own history, not the camp feed
    assert ("activity", "update") not in kinds
    assert not any(e["entity_type"] in {"slot", "todo", "material_need", "assignment"} for e in entries)


def test_audit_links_live_entities_only(client, history):
    slug, other = history["slug"], history["other_id"]

    def about_other():
        return [e for e in audit(client, slug, camp_level="true")
                if e["entity_type"] == "activity" and e["entity_id"] == other]

    created = about_other()[0]
    assert created["entity_url"].endswith(f"/activities/{other}")
    assert created["entity_title"] == "Druhá"

    ok(client.delete(f"/api/activities/{other}", headers=ADMIN))
    assert all(e["entity_url"] is None for e in about_other())


def test_audit_full_feed_prefixes_detail_entries_with_activity(client, history):
    entries = audit(client, history["slug"])
    # a per-activity detail entry carries its parent activity, so the camp feed can
    # prefix "[Akce] …"
    todo = next(e for e in entries if e["entity_type"] == "todo")
    assert todo["activity_title"] == "Nová akce"
    assert todo["activity_url"].endswith(f"/activities/{history['activity_id']}")
    act = next(e for e in entries if e["entity_type"] == "activity")
    assert act["activity_title"] is None


def test_audit_camp_level_shows_merge_linked_to_target(client, history):
    slug, src, dst = history["slug"], history["activity_id"], history["other_id"]
    ok(client.post(f"/api/activities/{src}/merge", json={"into": dst}, headers=ADMIN))

    merge = next(e for e in audit(client, slug, camp_level="true")
                 if e["entity_type"] == "activity" and e["action"] == "merge")
    assert merge["entity_id"] == dst
    assert merge["changes"]["merged_from"][0] == "Nová akce"
    assert merge["entity_url"].endswith(f"/activities/{dst}")


def test_audit_pagination_walks_older_entries(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    for i in range(5):
        client.patch(f"/api/activities/{aid}", json={"title": f"t{i}"}, headers=ADMIN)

    seen, before, pages = [], None, 0
    while True:
        url = f"/api/camps/{slug}/audit?limit=2" + (f"&before={before}" if before else "")
        body = get_json(client, url)
        seen.extend(e["id"] for e in body["entries"])
        pages += 1
        before = body["next_before"]
        if before is None:
            break
    # strictly descending across pages, no overlaps, and pagination actually happened
    assert seen == sorted(set(seen), reverse=True)
    assert pages >= 3 and len(seen) >= 5
