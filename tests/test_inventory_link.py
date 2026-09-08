"""Camp materials standing for warehouse things: the link, its uniqueness per camp, what
survives retiring or erasing the thing, and a check that follows up on a camp.
"""

from __future__ import annotations


from camp_planner.extensions import db, db_session
from camp_planner.models.audit import AuditLog, EntityType
from camp_planner.models.material import Material
from tests.conftest import (
    ADMIN,
    box_state,
    discard,
    editor,
    get_item,
    make_item,
    make_camp,
    page_data,
    start_check,
    upload,
    viewer,
)


def material_from(client, slug, item_id, headers=ADMIN, **fields):
    return client.post(f"/api/camps/{slug}/materials",
                       json={"inventory_item_id": item_id, **fields}, headers=headers)


def make_material(client, slug, name, **fields) -> dict:
    resp = client.post(f"/api/camps/{slug}/materials", json={"name": name, **fields}, headers=ADMIN)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["material"]


def patch_material(client, slug, material_id, **fields):
    return client.patch(f"/api/camps/{slug}/materials/{material_id}", json=fields, headers=ADMIN)


def add_need(client, activity_id, material_id, **fields) -> dict:
    resp = client.post(f"/api/activities/{activity_id}/materials",
                       json={"material_id": material_id, **fields}, headers=ADMIN)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["need"]


def start_camp_check(client, camp_id, name="Po táboře") -> dict:
    resp = client.post("/api/inventory/checks", json={"name": name, "camp_id": camp_id},
                       headers=ADMIN)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["check"]


def catalog(client, slug) -> list[dict]:
    return client.get(f"/api/camps/{slug}/materials", headers=ADMIN).get_json()["materials"]


def link_of(client, slug, material_id) -> dict | None:
    return next(m for m in catalog(client, slug) if m["id"] == material_id)["inventory_item"]


# --- making a material from a thing -----------------------------------------

def test_material_made_from_a_thing_copies_its_fields_and_links(client, seeded, box):
    item = make_item(client, box["id"], "Izolepa", unit="role", url="https://shop/izolepa", count=12)
    resp = material_from(client, seeded["slug"], item["id"])
    assert resp.status_code == 200, resp.get_json()
    m = resp.get_json()["material"]
    assert (m["name"], m["unit"], m["url"]) == ("Izolepa", "role", "https://shop/izolepa")
    link = m["inventory_item"]
    assert link["id"] == item["id"] and link["count"] == 12 and link["unit"] == "role"
    assert link["box"]["name"] == box["name"] and link["discarded_at"] is None


def test_sent_fields_win_over_the_thing(client, seeded, box):
    item = make_item(client, box["id"], "Izolepa", unit="role")
    m = material_from(client, seeded["slug"], item["id"], name="Lepicí páska", unit="ks").get_json()["material"]
    assert m["name"] == "Lepicí páska" and m["unit"] == "ks"
    assert m["inventory_item"]["id"] == item["id"]


def test_a_material_needs_a_name_or_a_thing(client, seeded):
    resp = client.post(f"/api/camps/{seeded['slug']}/materials", json={"unit": "ks"}, headers=ADMIN)
    assert resp.status_code == 422   # a schema rule, so spectree answers


def test_a_blank_name_falls_back_to_the_thing(client, seeded, box):
    item = make_item(client, box["id"], "Izolepa")
    m = material_from(client, seeded["slug"], item["id"], name="   ").get_json()["material"]
    assert m["name"] == "Izolepa"
    resp = client.post(f"/api/camps/{seeded['slug']}/materials", json={"name": "   "}, headers=ADMIN)
    assert resp.status_code == 400


def test_same_named_material_is_linked_instead_of_duplicated(client, seeded, box):
    slug = seeded["slug"]
    typed = make_material(client, slug, "izolepa", unit="ks", url="https://mine", note="moje")
    item = make_item(client, box["id"], "Izolepa", unit="role", url="https://shop")
    m = material_from(client, slug, item["id"], note="z věci").get_json()["material"]
    # The typed material is kept as typed; only the link is new.
    assert m["id"] == typed["id"] and m["unit"] == "ks" and m["url"] == "https://mine"
    assert m["note"] == "moje"
    assert m["inventory_item"]["id"] == item["id"]
    assert len(catalog(client, slug)) == 1
    # Doing it again changes nothing.
    again = material_from(client, slug, item["id"])
    assert again.status_code == 200 and again.get_json()["material"]["id"] == typed["id"]


def test_same_named_material_linked_elsewhere_is_refused(client, seeded, box):
    slug = seeded["slug"]
    other = make_item(client, box["id"], "Páska")
    typed = make_material(client, slug, "Izolepa", inventory_item_id=other["id"])
    assert typed["inventory_item"]["id"] == other["id"]
    item = make_item(client, box["id"], "izolepa")
    resp = material_from(client, slug, item["id"])
    assert resp.status_code == 400
    assert "jinou věc" in resp.get_json()["error"]


# --- one material per thing within a camp -----------------------------------

def test_a_thing_has_one_material_per_camp(client, seeded, box):
    slug = seeded["slug"]
    item = make_item(client, box["id"], "Izolepa")
    make_material(client, slug, "Izolepa", inventory_item_id=item["id"])
    # Neither a second material made from it, nor a relink onto it.
    assert material_from(client, slug, item["id"], name="Lepenka").status_code == 400
    other = make_material(client, slug, "Páska")
    resp = patch_material(client, slug, other["id"], inventory_item_id=item["id"])
    assert resp.status_code == 400
    assert link_of(client, slug, other["id"]) is None
    # Another camp's catalog is another camp's.
    make_camp(client, "u")
    assert material_from(client, "u", item["id"]).status_code == 200


def test_the_link_is_edited_by_patch_and_audited(client, seeded, box):
    slug = seeded["slug"]
    lano = make_item(client, box["id"], "Lano")
    pasky = make_item(client, box["id"], "Pásky")
    m = make_material(client, slug, "Provaz")
    assert patch_material(client, slug, m["id"], inventory_item_id=lano["id"]).status_code == 200
    assert link_of(client, slug, m["id"])["id"] == lano["id"]
    assert patch_material(client, slug, m["id"], inventory_item_id=pasky["id"]).status_code == 200
    assert link_of(client, slug, m["id"])["id"] == pasky["id"]
    # An unrelated patch leaves the link alone; an explicit null drops it.
    assert patch_material(client, slug, m["id"], note="x").status_code == 200
    assert link_of(client, slug, m["id"])["id"] == pasky["id"]
    assert patch_material(client, slug, m["id"], inventory_item_id=None).status_code == 200
    assert link_of(client, slug, m["id"]) is None
    diffs = [row.changes.get("inventory_item") for row in db_session.scalars(
        db.select(AuditLog).where(AuditLog.entity_type == EntityType.material,
                                  AuditLog.entity_id == m["id"]).order_by(AuditLog.id)).all()]
    assert [d for d in diffs if d] == [[None, "Lano"], ["Lano", "Pásky"], ["Pásky", None]]


def test_a_colliding_rename_beside_a_relink_is_still_a_plain_error(client, seeded, box):
    slug = seeded["slug"]
    alfa = make_material(client, slug, "Alfa")
    make_material(client, slug, "Beta")
    item = make_item(client, box["id"], "Lano")
    resp = patch_material(client, slug, alfa["id"], name="Beta", inventory_item_id=item["id"])
    assert resp.status_code == 400
    assert "už v katalogu existuje" in resp.get_json()["error"]


def test_unknown_or_retired_things_cannot_be_linked(client, seeded, box):
    slug = seeded["slug"]
    m = make_material(client, slug, "Provaz")
    assert patch_material(client, slug, m["id"], inventory_item_id=9999).status_code == 400
    assert material_from(client, slug, 0).status_code == 400        # no thing 0, and no name either
    item = make_item(client, box["id"], "Lano")
    discard(client, item)
    assert patch_material(client, slug, m["id"], inventory_item_id=item["id"]).status_code == 400
    assert material_from(client, slug, item["id"]).status_code == 400


# --- what happens to the link when the thing goes ----------------------------

def test_retiring_the_thing_keeps_the_link(client, seeded, box):
    slug = seeded["slug"]
    item = make_item(client, box["id"], "Lano")
    m = make_material(client, slug, "Lano", inventory_item_id=item["id"])
    discard(client, item)
    link = link_of(client, slug, m["id"])
    assert link["id"] == item["id"] and link["discarded_at"] and link["box"] is None


def test_erasing_the_thing_leaves_the_material_unlinked(client, seeded, box):
    slug = seeded["slug"]
    item = make_item(client, box["id"], "Lano")
    m = make_material(client, slug, "Lano", inventory_item_id=item["id"])
    assert client.delete(f"/api/inventory/items/{item['id']}", headers=ADMIN).status_code == 200
    assert link_of(client, slug, m["id"]) is None
    assert db_session.get(Material, m["id"]) is not None


def test_merge_passes_the_link_to_an_unlinked_target(client, seeded, box):
    slug = seeded["slug"]
    lano = make_item(client, box["id"], "Lano")
    provaz = make_item(client, box["id"], "Provaz")
    linked = make_material(client, slug, "Lano", inventory_item_id=lano["id"])
    bare = make_material(client, slug, "Šňůra")
    resp = client.post(f"/api/camps/{slug}/materials/{linked['id']}/merge",
                       json={"into": bare["id"]}, headers=ADMIN)
    assert resp.status_code == 200, resp.get_json()
    assert link_of(client, slug, bare["id"])["id"] == lano["id"]
    entries = client.get(f"/api/camps/{slug}/audit", headers=ADMIN).get_json()["entries"]
    merged = next(e for e in entries if e["action"] == "merge")
    assert merged["changes"]["inventory_item"] == [None, "Lano"]
    # A target with its own thing keeps it.
    own = make_material(client, slug, "Provázek", inventory_item_id=provaz["id"])
    resp = client.post(f"/api/camps/{slug}/materials/{bare['id']}/merge",
                       json={"into": own["id"]}, headers=ADMIN)
    assert resp.status_code == 200, resp.get_json()
    assert link_of(client, slug, own["id"])["id"] == provaz["id"]


def test_a_need_carries_the_link(client, seeded, box):
    item = make_item(client, box["id"], "Lano", count=3)
    m = make_material(client, seeded["slug"], "Lano", inventory_item_id=item["id"])
    need = add_need(client, seeded["activity_id"], m["id"], amount=2)
    assert need["material"]["inventory_item"]["box"]["name"] == box["name"]


def test_a_blank_unit_override_is_no_override(client, seeded):
    """Stored as NULL, so the need falls into the material's own unit bucket: the sum the
    box column shows and the one the materials page shows are then the same."""
    m = make_material(client, seeded["slug"], "Izolepa", unit="ks")
    need = add_need(client, seeded["activity_id"], m["id"], amount=2, unit="   ")
    assert need["unit"] is None


def test_relinking_the_same_thing_writes_no_audit_row(client, seeded, box):
    slug = seeded["slug"]
    item = make_item(client, box["id"], "Izolepa")
    m = make_material(client, slug, "Izolepa", inventory_item_id=item["id"])
    assert material_from(client, slug, item["id"]).status_code == 200
    rows = db_session.scalars(db.select(AuditLog).where(
        AuditLog.entity_type == EntityType.material, AuditLog.entity_id == m["id"])).all()
    assert len(rows) == 1     # the create; the second link changed nothing


# --- the picker ----------------------------------------------------------------

def test_picker_lists_live_things_with_their_box(client, seeded, box):
    live = make_item(client, box["id"], "Lano", alt_names=["provaz"], unit="m", url="https://shop/lano")
    gone = make_item(client, box["id"], "Stan")
    discard(client, gone)
    assert client.get("/api/inventory/items").status_code == 401
    resp = client.get("/api/inventory/items", headers=viewer("t"))
    assert resp.status_code == 200
    items = resp.get_json()["items"]
    assert [i["name"] for i in items] == ["Lano"]
    assert items[0] == {"id": live["id"], "name": "Lano", "alt_names": ["provaz"], "unit": "m",
                        "url": "https://shop/lano", "count": None, "note": None,
                        "discarded_at": None, "box_id": box["id"], "photos": [],
                        "box": {"id": box["id"], "name": box["name"], "location": None,
                                "note": None, "virtual": False}}


def test_the_link_and_the_picker_carry_the_title_photo(client, seeded, box, media_dir):
    """The camp pages show one thumbnail, so the title photo has to come first, also after
    somebody promotes another photo to be it."""
    item = make_item(client, box["id"], "Lano")
    assert upload(client, item["id"]).status_code == 200
    assert upload(client, item["id"]).status_code == 200
    second = get_item(client, item["id"])["photos"][1]
    assert client.post(f"/api/inventory/photos/{second['id']}/title", headers=ADMIN).status_code == 200
    m = make_material(client, seeded["slug"], "Lano", inventory_item_id=item["id"])
    photos = link_of(client, seeded["slug"], m["id"])["photos"]
    assert photos[0]["filename"] == second["filename"] and len(photos) == 2
    picked = client.get("/api/inventory/items", headers=ADMIN).get_json()["items"][0]
    assert picked["photos"][0]["filename"] == second["filename"] and len(picked["photos"]) == 2


# --- a check that follows up on a camp ----------------------------------------

def activity(client, slug, title) -> int:
    resp = client.post(f"/api/camps/{slug}/activities", json={"title": title}, headers=ADMIN)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["activity"]["id"]


def test_check_with_a_camp_shows_what_it_took(client, seeded, box):
    slug, camp_id = seeded["slug"], seeded["camp_id"]
    izolepa = make_item(client, box["id"], "Izolepa", count=10)
    lano = make_item(client, box["id"], "Lano")
    make_item(client, box["id"], "Stan")                     # never linked
    tape = make_material(client, slug, "Izolepa", unit="ks", inventory_item_id=izolepa["id"])
    rope = make_material(client, slug, "Lano", unit="m", inventory_item_id=lano["id"])
    patch_material(client, slug, rope["id"], sum_strategy="max")   # shared: the largest need counts
    a1, a2 = seeded["activity_id"], activity(client, slug, "Druhá")
    add_need(client, a1, tape["id"], amount=3)
    add_need(client, a2, tape["id"], amount=4)
    add_need(client, a1, rope["id"], amount=5, unit="m")
    add_need(client, a2, rope["id"], amount=20)
    add_need(client, a1, make_material(client, slug, "Kolíky")["id"], amount=1)   # unlinked

    # No camp on the check: nothing to say.
    check = start_check(client)
    assert box_state(client, box["id"])["taken"] == []
    client.delete(f"/api/inventory/checks/{check['id']}", headers=ADMIN)

    resp = client.post("/api/inventory/checks", json={"name": "Po táboře", "camp_id": camp_id}, headers=ADMIN)
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["check"]["camp"] == {"id": camp_id, "name": "Tábor", "slug": slug}
    state = box_state(client, box["id"])
    assert state["active_check"]["camp"]["name"] == "Tábor"
    taken = {t["item_id"]: t for t in state["taken"]}
    assert taken[izolepa["id"]]["totals"] == [{"amount": 7, "unit": "ks"}]
    assert taken[izolepa["id"]]["activities"] == 2
    assert taken[lano["id"]]["totals"] == [{"amount": 20, "unit": "m"}]
    assert set(taken) == {izolepa["id"], lano["id"]}


def test_needs_in_other_units_are_summed_apart(client, seeded, box):
    slug = seeded["slug"]
    item = make_item(client, box["id"], "Izolepa")
    tape = make_material(client, slug, "Izolepa", unit="ks", inventory_item_id=item["id"])
    add_need(client, seeded["activity_id"], tape["id"], amount=3)
    add_need(client, activity(client, slug, "Druhá"), tape["id"], amount=2, unit="bal")
    client.post("/api/inventory/checks", json={"name": "Po táboře", "camp_id": seeded["camp_id"]}, headers=ADMIN)
    taken, = box_state(client, box["id"])["taken"]
    assert taken == {"item_id": item["id"], "activities": 2,
                     "totals": [{"amount": 3, "unit": "ks"}, {"amount": 2, "unit": "bal"}]}


def test_a_material_nobody_asked_an_amount_of_is_left_out(client, seeded, box):
    """Needs without amounts have no number to show, so the thing gets no camp column."""
    slug = seeded["slug"]
    item = make_item(client, box["id"], "Izolepa")
    tape = make_material(client, slug, "Izolepa", inventory_item_id=item["id"])
    add_need(client, seeded["activity_id"], tape["id"])
    client.post("/api/inventory/checks", json={"name": "Po táboře", "camp_id": seeded["camp_id"]}, headers=ADMIN)
    assert box_state(client, box["id"])["taken"] == []


def test_another_camps_material_on_the_same_thing_is_not_counted(client, seeded, box):
    """One thing may back a material in every camp; a check counts its own camp's."""
    slug = seeded["slug"]
    item = make_item(client, box["id"], "Izolepa")
    tape = make_material(client, slug, "Izolepa", unit="ks", inventory_item_id=item["id"])
    add_need(client, seeded["activity_id"], tape["id"], amount=3)
    make_camp(client, "u")
    other = make_material(client, "u", "Izolepa", unit="ks", inventory_item_id=item["id"])
    add_need(client, activity(client, "u", "Jiná"), other["id"], amount=99)
    start_camp_check(client, seeded["camp_id"])
    taken, = box_state(client, box["id"])["taken"]
    assert taken == {"item_id": item["id"], "activities": 1,
                     "totals": [{"amount": 3, "unit": "ks"}]}


def test_only_the_activities_that_named_an_amount_are_counted(client, seeded, box):
    slug = seeded["slug"]
    item = make_item(client, box["id"], "Izolepa")
    tape = make_material(client, slug, "Izolepa", unit="ks", inventory_item_id=item["id"])
    add_need(client, seeded["activity_id"], tape["id"])                        # no amount
    add_need(client, activity(client, slug, "Druhá"), tape["id"], amount=3)
    start_camp_check(client, seeded["camp_id"])
    taken, = box_state(client, box["id"])["taken"]
    assert taken == {"item_id": item["id"], "activities": 1,
                     "totals": [{"amount": 3, "unit": "ks"}]}


def test_a_check_follows_up_only_on_a_camp_you_may_see(client, seeded, box):
    """Editing the warehouse is not a camp grant: an editor elsewhere walks the warehouse
    but cannot point a check at somebody else's camp, nor is it offered to them."""
    make_camp(client, "u")
    resp = client.post("/api/inventory/checks",
                       json={"name": "Po táboře", "camp_id": seeded["camp_id"]}, headers=editor("u"))
    assert resp.status_code == 400
    offered = page_data(client, "/inventory/checks", headers=editor("u"))["camps"]
    assert [c["slug"] for c in offered] == ["u"]


def test_the_camp_column_is_read_by_whoever_reads_the_warehouse(client, seeded, box):
    """Deliberate: the walk is done by whoever stands in the storeroom, camp grant or not."""
    item = make_item(client, box["id"], "Izolepa")
    tape = make_material(client, seeded["slug"], "Izolepa", unit="ks", inventory_item_id=item["id"])
    add_need(client, seeded["activity_id"], tape["id"], amount=3)
    start_camp_check(client, seeded["camp_id"])
    make_camp(client, "u")
    state = box_state(client, box["id"], headers=viewer("u"))
    assert state["active_check"]["camp"]["slug"] == seeded["slug"]
    assert state["taken"][0]["totals"] == [{"amount": 3, "unit": "ks"}]


def test_the_camp_column_links_to_the_camp_for_its_viewers(client, seeded, box):
    start_camp_check(client, seeded["camp_id"])
    make_camp(client, "u")
    link = page_data(client, f"/inventory/boxes/{box['id']}", headers=viewer(seeded["slug"]))
    assert link["camp_materials"] == {"camp_id": seeded["camp_id"],
                                      "url": f"/camps/{seeded['slug']}/materials"}
    stranger = page_data(client, f"/inventory/boxes/{box['id']}", headers=viewer("u"))
    assert stranger["camp_materials"] is None


def test_deleting_the_camp_leaves_the_check_behind(client, seeded, box):
    """SET NULL: the check outlives the camp it followed, minus the column."""
    item = make_item(client, box["id"], "Lano")
    make_material(client, seeded["slug"], "Lano", inventory_item_id=item["id"])
    start_camp_check(client, seeded["camp_id"])
    client.delete(f"/api/activities/{seeded['activity_id']}", headers=ADMIN)
    assert client.delete(f"/api/camps/{seeded['slug']}", headers=ADMIN).status_code == 200
    state = box_state(client, box["id"])
    assert state["active_check"]["camp"] is None and state["taken"] == []


def test_a_check_needs_a_real_camp(client, seeded):
    resp = client.post("/api/inventory/checks", json={"name": "X", "camp_id": 9999}, headers=ADMIN)
    assert resp.status_code == 400
    assert page_data(client, "/inventory/checks")["active_check"] is None


def test_checks_page_offers_camps_newest_first(client, seeded):
    make_camp(client, "u", start_date="2027-07-01")
    make_camp(client, "s", start_date="2025-07-01")
    data = page_data(client, "/inventory/checks")
    assert [c["slug"] for c in data["camps"]] == ["u", "t", "s"]
    check = start_check(client)
    assert check["camp"] is None
    assert page_data(client, "/inventory/checks")["active_check"]["camp"] is None
