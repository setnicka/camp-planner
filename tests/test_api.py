"""End-to-end tests for the JSON API blueprint (mounted at /api).

Exercises the request → service → response path for each resource, the optimistic
lock, and the permission envelope (anonymous 401 / viewer 403 / editor allowed).
"""

from __future__ import annotations

from sqlalchemy import event

from camp_planner.extensions import db
from tests.conftest import ADMIN, editor, viewer


def _json(resp):
    return resp.get_json()


def _get(client, url, headers=ADMIN):
    """GET url and return the JSON body (for read-back verification)."""
    resp = client.get(url, headers=headers)
    assert resp.status_code == 200
    return _json(resp)


def _count_queries(fn) -> int:
    """Count SQL statements issued while calling fn() (for N+1 regression checks)."""
    statements: list[str] = []

    def listener(conn, cursor, statement, *_, **__):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", listener)
    try:
        fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", listener)
    return len(statements)


def _make_slot(client, slug, activity_id, *, start="2026-07-04T14:00", end="2026-07-04T16:00", role="main"):
    """Create a slot via the timeline batch (the only placement path) and return its id."""
    resp = client.patch(f"/api/camps/{slug}/timeline", json={
        "creates": [{"activity_id": activity_id, "role": role, "start_at": start, "end_at": end}]},
        headers=ADMIN)
    return _json(resp)["created"][0]["id"]


# --- timeline ----------------------------------------------------------------

def test_timeline_get_returns_payload(client, seeded):
    body = _get(client, f"/api/camps/{seeded['slug']}/timeline")
    assert body["ok"] and body["camp"]["slug"] == "t"
    assert body["segments"] == []  # no slots yet


def test_timeline_save_conflict_on_stale_rev(client, seeded):
    url = f"/api/camps/{seeded['slug']}/timeline"
    resp = client.patch(url, json={"rev": 999, "moves": []}, headers=ADMIN)
    assert resp.status_code == 409
    body = _json(resp)
    assert not body["ok"] and "timeline" in body and body["rev"] == 0


def test_timeline_batch_create_move_delete(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    s1 = _make_slot(client, slug, aid)
    rev = _get(client, f"/api/camps/{slug}/timeline")["camp"]["rev"]

    resp = client.patch(f"/api/camps/{slug}/timeline", json={
        "rev": rev,
        "creates": [{"activity_id": aid, "role": "prep",
                     "start_at": "2026-07-04T13:30", "end_at": "2026-07-04T14:00"}],
        "moves": [{"slot_id": s1, "start_at": "2026-07-04T15:00", "end_at": "2026-07-04T17:00"}],
    }, headers=ADMIN)
    assert resp.status_code == 200
    body = _json(resp)
    assert body["rev"] == rev + 1
    assert len(body["created"]) == 1 and body["created"][0]["role"] == "prep"
    new_id = body["created"][0]["id"]
    tl = _get(client, f"/api/camps/{slug}/timeline")
    assert len(tl["segments"]) == 2 and tl["camp"]["rev"] == rev + 1

    # delete both in a follow-up batch -> timeline empty
    resp = client.patch(f"/api/camps/{slug}/timeline",
                       json={"rev": body["rev"], "deletes": [s1, new_id]}, headers=ADMIN)
    assert resp.status_code == 200
    assert _get(client, f"/api/camps/{slug}/timeline")["segments"] == []


def test_timeline_retype_changes_role_and_audits(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    s1 = _make_slot(client, slug, aid, role="main")
    rev = _get(client, f"/api/camps/{slug}/timeline")["camp"]["rev"]

    resp = client.patch(f"/api/camps/{slug}/timeline",
                        json={"rev": rev, "retypes": [{"slot_id": s1, "role": "cleanup"}]}, headers=ADMIN)
    assert resp.status_code == 200 and _json(resp)["rev"] == rev + 1
    assert _get(client, f"/api/camps/{slug}/timeline")["segments"][0]["role"] == "cleanup"

    # one slot-level audit row carrying the role diff
    slot_rows = _json(client.get(f"/api/camps/{slug}/audit?entity_type=slot", headers=ADMIN))["entries"]
    role_rows = [e for e in slot_rows if e["action"] == "update" and e["changes"] and "role" in e["changes"]]
    assert len(role_rows) == 1 and role_rows[0]["changes"]["role"] == ["main", "cleanup"]

    # a no-op retype (same role) changes nothing and writes no audit row
    rev = _get(client, f"/api/camps/{slug}/timeline")["camp"]["rev"]
    client.patch(f"/api/camps/{slug}/timeline",
                 json={"rev": rev, "retypes": [{"slot_id": s1, "role": "cleanup"}]}, headers=ADMIN)
    slot_rows = _json(client.get(f"/api/camps/{slug}/audit?entity_type=slot", headers=ADMIN))["entries"]
    assert len([e for e in slot_rows if e["action"] == "update" and e["changes"] and "role" in e["changes"]]) == 1


def test_timeline_create_rejects_foreign_activity(client, seeded):
    slug = seeded["slug"]
    resp = client.patch(f"/api/camps/{slug}/timeline", json={
        "creates": [{"activity_id": 99999, "start_at": "2026-07-04T13:00", "end_at": "2026-07-04T14:00"}],
    }, headers=ADMIN)
    assert resp.status_code == 400


def test_timeline_create_rejects_end_before_start(client, seeded):
    # end<=start is a schema model_validator on TimelineCreate -> 422
    resp = client.patch(f"/api/camps/{seeded['slug']}/timeline", json={
        "creates": [{"activity_id": seeded["activity_id"],
                     "start_at": "2026-07-04T16:00", "end_at": "2026-07-04T14:00"}],
    }, headers=ADMIN)
    assert resp.status_code == 422


# --- activities --------------------------------------------------------------

def test_activity_create_requires_title(client, seeded):
    # missing title fails schema validation -> 422 pydantic error list
    resp = client.post(f"/api/camps/{seeded['slug']}/activities", json={}, headers=ADMIN)
    assert resp.status_code == 422
    assert any("title" in e["loc"] for e in _json(resp))


def test_activity_list(client, seeded):
    slug = seeded["slug"]
    client.post(f"/api/camps/{slug}/activities", json={"title": "Druhá hra"}, headers=ADMIN)
    body = _get(client, f"/api/camps/{slug}/activities")
    titles = [a["title"] for a in body["activities"]]
    assert seeded["activity_id"] in [a["id"] for a in body["activities"]]
    assert "Druhá hra" in titles
    # full ActivityOut shape per item (nested collections present)
    assert "slots" in body["activities"][0] and "material_needs" in body["activities"][0]


# --- assignments + tags ------------------------------------------------------

def test_set_orgs_and_tags(client, seeded):
    aid = seeded["activity_id"]
    resp = client.put(f"/api/activities/{aid}/orgs",
                      json={"orgs": [{"org_id": seeded["org_id"], "role": "garant"}]}, headers=ADMIN)
    assert resp.status_code == 200
    assert _json(resp)["orgs"][0]["initials"] == "K"

    resp = client.put(f"/api/activities/{aid}/tags",
                      json={"tags": [{"tag_id": seeded["tag_id"], "value": "ano"}]}, headers=ADMIN)
    assert resp.status_code == 200
    assert _json(resp)["tags"][0]["value"] == "ano"


def test_set_tags_records_value_diff_in_audit(client, seeded):
    slug, aid, tid = seeded["slug"], seeded["activity_id"], seeded["tag_id"]  # tag "Důležité", kind text
    tag_rows = lambda: [e for e in _json(  # noqa: E731
        client.get(f"/api/camps/{slug}/audit?entity_type=tag", headers=ADMIN))["entries"] if e["changes"]]

    client.put(f"/api/activities/{aid}/tags", json={"tags": [{"tag_id": tid, "value": "ano"}]}, headers=ADMIN)
    assert tag_rows()[0]["changes"] == {"Důležité": [None, "ano"]}          # added
    client.put(f"/api/activities/{aid}/tags", json={"tags": [{"tag_id": tid, "value": "ne"}]}, headers=ADMIN)
    assert tag_rows()[0]["changes"] == {"Důležité": ["ano", "ne"]}          # value changed
    client.put(f"/api/activities/{aid}/tags", json={"tags": []}, headers=ADMIN)
    assert tag_rows()[0]["changes"] == {"Důležité": ["ne", None]}           # removed

    n = len(tag_rows())
    client.put(f"/api/activities/{aid}/tags", json={"tags": []}, headers=ADMIN)  # no-op
    assert len(tag_rows()) == n                                             # writes nothing


def test_set_orgs_rejects_duplicate(client, seeded):
    # same (org_id, role) twice -> schema validator -> 422 (malformed body)
    oid = seeded["org_id"]
    resp = client.put(f"/api/activities/{seeded['activity_id']}/orgs",
                      json={"orgs": [{"org_id": oid, "role": "garant"},
                                     {"org_id": oid, "role": "garant"}]}, headers=ADMIN)
    assert resp.status_code == 422


def test_set_tags_rejects_duplicate(client, seeded):
    tid = seeded["tag_id"]
    resp = client.put(f"/api/activities/{seeded['activity_id']}/tags",
                      json={"tags": [{"tag_id": tid}, {"tag_id": tid}]}, headers=ADMIN)
    assert resp.status_code == 422


def test_tag_value_patch(client, seeded):
    aid, tid = seeded["activity_id"], seeded["tag_id"]
    # apply the tag first (membership), then update its value via the per-tag PATCH
    client.put(f"/api/activities/{aid}/tags", json={"tags": [{"tag_id": tid}]}, headers=ADMIN)
    resp = client.patch(f"/api/activities/{aid}/tags/{tid}", json={"value": "hotovo"}, headers=ADMIN)
    assert resp.status_code == 200
    assert _json(resp)["tag"]["value"] == "hotovo"  # seeded tag is a label/text → free value


def test_tag_value_patch_404_when_not_applied(client, seeded):
    # tag exists in the camp but isn't applied to the activity
    resp = client.patch(f"/api/activities/{seeded['activity_id']}/tags/{seeded['tag_id']}",
                        json={"value": "x"}, headers=ADMIN)
    assert resp.status_code == 404


def test_tag_value_validated_per_kind(client, seeded):
    s, aid = seeded["slug"], seeded["activity_id"]
    saved = _json(client.put(f"/api/camps/{s}/tags",
                  json={"items": [{"name": "Postup", "kind": "progress"},
                                  {"name": "Štítek", "kind": "label"}]}, headers=ADMIN))["items"]
    prog = next(t["id"] for t in saved if t["name"] == "Postup")
    label = next(t["id"] for t in saved if t["name"] == "Štítek")
    client.put(f"/api/activities/{aid}/tags",
               json={"tags": [{"tag_id": prog}, {"tag_id": label}]}, headers=ADMIN)

    assert client.patch(f"/api/activities/{aid}/tags/{prog}", json={"value": "200"}, headers=ADMIN).status_code == 400
    ok = client.patch(f"/api/activities/{aid}/tags/{prog}", json={"value": "60"}, headers=ADMIN)
    assert ok.status_code == 200 and _json(ok)["tag"]["value"] == "60"
    assert client.patch(f"/api/activities/{aid}/tags/{label}", json={"value": "x"}, headers=ADMIN).status_code == 400


def test_activity_orgs_reject_foreign(client, seeded):
    resp = client.put(f"/api/activities/{seeded['activity_id']}/orgs",
                      json={"orgs": [{"org_id": 99999, "role": "garant"}]}, headers=ADMIN)
    assert resp.status_code == 400


def test_activity_delete_blocked_while_it_has_slots(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    _make_slot(client, slug, aid)
    resp = client.delete(f"/api/activities/{aid}", headers=ADMIN)
    assert resp.status_code == 400 and "naplánované sloty" in _json(resp)["error"]
    # the activity survives for the user to clear its slots first
    assert aid in [a["id"] for a in _get(client, f"/api/camps/{slug}/activities")["activities"]]


def test_activity_delete_when_slotless(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    resp = client.delete(f"/api/activities/{aid}", headers=ADMIN)
    assert resp.status_code == 200 and _json(resp)["id"] == aid
    assert aid not in [a["id"] for a in _get(client, f"/api/camps/{slug}/activities")["activities"]]


def test_activity_merge_transfers_slots_todos_and_joins_needs(client, seeded):
    slug, src = seeded["slug"], seeded["activity_id"]
    dst = _json(client.post(f"/api/camps/{slug}/activities", json={"title": "Cíl"}, headers=ADMIN))["activity"]["id"]
    # source carries a slot, a todo and a material need; the target uses the same material
    _make_slot(client, slug, src)
    client.post(f"/api/activities/{src}/todos", json={"title": "Koupit lano"}, headers=ADMIN)
    mid = _make_material(client, slug, name="lano", unit="m")["material"]["id"]
    client.post(f"/api/activities/{src}/materials", json={"material_id": mid, "amount": 30}, headers=ADMIN)
    client.post(f"/api/activities/{dst}/materials", json={"material_id": mid, "amount": 20}, headers=ADMIN)

    resp = client.post(f"/api/activities/{src}/merge", json={"into": dst}, headers=ADMIN)
    assert resp.status_code == 200 and _json(resp)["activity"]["id"] == dst

    # source gone; its slot + todo moved to the target, the shared need's amount summed
    acts = {a["id"]: a for a in _get(client, f"/api/camps/{slug}/activities")["activities"]}
    assert src not in acts
    target = _get(client, f"/api/activities/{dst}")["activity"]
    assert len(target["slots"]) == 1
    assert [t["title"] for t in target["todos"]] == ["Koupit lano"]
    assert len(target["material_needs"]) == 1 and target["material_needs"][0]["amount"] == 50  # 30 + 20


def test_activity_merge_rejects_cross_camp(client, seeded):
    src = seeded["activity_id"]
    other = _json(client.post("/api/camps", json={**_NEW_CAMP, "slug": "jina"}, headers=ADMIN))
    dst = _json(client.post("/api/camps/jina/activities", json={"title": "Jiná"}, headers=ADMIN))["activity"]["id"]
    assert other  # camp created
    resp = client.post(f"/api/activities/{src}/merge", json={"into": dst}, headers=ADMIN)
    assert resp.status_code == 400 and "různých akcí" in _json(resp)["error"]


def test_slot_orgs_set_and_reject_foreign(client, seeded):
    slug, aid, oid = seeded["slug"], seeded["activity_id"], seeded["org_id"]
    slot_id = _make_slot(client, slug, aid)
    resp = client.put(f"/api/slots/{slot_id}/orgs", json={"org_ids": [oid]}, headers=ADMIN)
    assert resp.status_code == 200
    assert [o["initials"] for o in _json(resp)["orgs"]] == ["K"]
    assert client.put(f"/api/slots/{slot_id}/orgs", json={"org_ids": [99999]}, headers=ADMIN).status_code == 400

    # audited as a `slot` change (under the activity) with the orgs before/after
    def org_audits():
        rows = _json(client.get(f"/api/camps/{slug}/audit?entity_type=slot", headers=ADMIN))["entries"]
        return [e for e in rows if e["changes"] and "orgs" in e["changes"]]

    changes = org_audits()
    assert len(changes) == 1
    assert changes[0]["activity_id"] == aid and changes[0]["changes"]["orgs"] == [[], ["K"]]

    # re-submitting the same set is a no-op: no second audit row
    client.put(f"/api/slots/{slot_id}/orgs", json={"org_ids": [oid]}, headers=ADMIN)
    assert len(org_audits()) == 1


# --- todos -------------------------------------------------------------------

def test_todo_lifecycle(client, seeded):
    aid = seeded["activity_id"]
    resp = client.post(f"/api/activities/{aid}/todos", json={"title": "Koupit lano"}, headers=ADMIN)
    todo_id = _json(resp)["todo"]["id"]

    resp = client.patch(f"/api/todos/{todo_id}", json={"is_done": True}, headers=ADMIN)
    assert _json(resp)["todo"]["is_done"] is True

    resp = client.delete(f"/api/todos/{todo_id}", headers=ADMIN)
    assert _json(resp)["id"] == todo_id


def test_todo_validation_returns_pydantic_error_list(client, seeded):
    # malformed body -> 422 + the pydantic error list (the adopted validation contract)
    resp = client.post(f"/api/activities/{seeded['activity_id']}/todos",
                       json={"title": ""}, headers=ADMIN)
    assert resp.status_code == 422
    body = resp.get_json()
    assert isinstance(body, list) and "title" in body[0]["loc"]


# --- materials ---------------------------------------------------------------

def _make_material(client, slug, name="A4 papír", unit="ks", **extra):
    resp = client.post(f"/api/camps/{slug}/materials",
                      json={"name": name, "unit": unit, **extra}, headers=ADMIN)
    return _json(resp)


def test_material_catalog_create_list_and_dedup(client, seeded):
    slug = seeded["slug"]
    body = _make_material(client, slug, note="bílý", url="https://shop/a4")
    assert body["ok"] and body["material"]["name"] == "A4 papír" and body["material"]["url"] == "https://shop/a4"

    # listing returns the catalog
    mats = _get(client, f"/api/camps/{slug}/materials")["materials"]
    assert [m["name"] for m in mats] == ["A4 papír"] and mats[0]["note"] == "bílý"

    # a normalized-equal name can't create a second catalog row
    resp = client.post(f"/api/camps/{slug}/materials", json={"name": "papír A4"}, headers=ADMIN)
    assert resp.status_code == 400


def test_material_need_add_by_id(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    material_id = _make_material(client, slug)["material"]["id"]
    resp = client.post(f"/api/activities/{aid}/materials",
                      json={"material_id": material_id, "amount": 10}, headers=ADMIN)
    assert resp.status_code == 200
    n = _json(resp)["need"]
    assert n["amount"] == 10 and n["material"]["name"] == "A4 papír" and n["material"]["unit"] == "ks"

    # can't add the same catalog material twice to one activity
    dup = client.post(f"/api/activities/{aid}/materials", json={"material_id": material_id}, headers=ADMIN)
    assert dup.status_code == 400


def test_material_merge_migrates_usages_and_pins_unit(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    # two catalog materials with different default units; activity uses the source (no override)
    src = _make_material(client, slug, name="papír", unit="ks")["material"]["id"]
    dst = _make_material(client, slug, name="kancelářský papír", unit="balení")["material"]["id"]
    client.post(f"/api/activities/{aid}/materials", json={"material_id": src, "amount": 5}, headers=ADMIN)

    resp = client.post(f"/api/camps/{slug}/materials/{src}/merge", json={"into": dst}, headers=ADMIN)
    assert resp.status_code == 200 and _json(resp)["material"]["id"] == dst

    # source gone from catalog; usage migrated to dst with the old default 'ks' pinned as override
    mats = _get(client, f"/api/camps/{slug}/materials")["materials"]
    assert src not in [m["id"] for m in mats]
    needs = _get(client, f"/api/activities/{aid}")["activity"]["material_needs"]
    assert len(needs) == 1
    assert needs[0]["material"]["id"] == dst and needs[0]["unit"] == "ks"  # effective unit preserved


def _two_materials_needed(client, slug, aid, *, src_unit="ks"):
    """Two catalog materials, both needed by the activity (50 dst + 20 src); returns (src, dst)."""
    dst = _make_material(client, slug, name="papíry", unit="ks")["material"]["id"]
    src = _make_material(client, slug, name="papíry A4", unit=src_unit)["material"]["id"]
    client.post(f"/api/activities/{aid}/materials", json={"material_id": dst, "amount": 50}, headers=ADMIN)
    client.post(f"/api/activities/{aid}/materials", json={"material_id": src, "amount": 20}, headers=ADMIN)
    return src, dst


def test_material_merge_sums_amounts_when_activity_uses_both(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    src, dst = _two_materials_needed(client, slug, aid)  # both needs in the same unit

    resp = client.post(f"/api/camps/{slug}/materials/{src}/merge", json={"into": dst}, headers=ADMIN)
    assert resp.status_code == 200

    needs = _get(client, f"/api/activities/{aid}")["activity"]["material_needs"]
    assert len(needs) == 1
    assert needs[0]["material"]["id"] == dst
    assert needs[0]["amount"] == 70  # 50 + 20, source's amount folded in, not dropped


def test_material_delete_unused(client, seeded):
    slug = seeded["slug"]
    mid = _make_material(client, slug, name="zbytečný")["material"]["id"]
    resp = client.delete(f"/api/camps/{slug}/materials/{mid}", headers=ADMIN)
    assert resp.status_code == 200 and _json(resp)["id"] == mid
    mats = [m["id"] for m in _get(client, f"/api/camps/{slug}/materials")["materials"]]
    assert mid not in mats


def test_material_delete_blocked_while_in_use(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    mid = _make_material(client, slug)["material"]["id"]
    client.post(f"/api/activities/{aid}/materials", json={"material_id": mid, "amount": 3}, headers=ADMIN)

    resp = client.delete(f"/api/camps/{slug}/materials/{mid}", headers=ADMIN)
    assert resp.status_code == 400 and "nelze smazat" in _json(resp)["error"]
    # material and its need both survive
    mats = [m["id"] for m in _get(client, f"/api/camps/{slug}/materials")["materials"]]
    assert mid in mats


def test_material_merge_fails_on_unit_mismatch(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    src, dst = _two_materials_needed(client, slug, aid, src_unit="balení")  # different effective units

    resp = client.post(f"/api/camps/{slug}/materials/{src}/merge", json={"into": dst}, headers=ADMIN)
    assert resp.status_code == 400
    # nothing changed: both materials and both needs survive for manual fixing
    mats = [m["id"] for m in _get(client, f"/api/camps/{slug}/materials")["materials"]]
    assert src in mats and dst in mats
    needs = _get(client, f"/api/activities/{aid}")["activity"]["material_needs"]
    assert len(needs) == 2


# --- camps (create + edit) ---------------------------------------------------

_NEW_CAMP = {
    "name": "Letní tábor", "start_date": "2026-08-01", "length_days": 5,
    "timezone": "Europe/Prague", "window_start_min": 240, "snap_minutes": 15,
}


def test_camp_create_starts_empty(client, app):
    resp = client.post("/api/camps", json=_NEW_CAMP, headers=ADMIN)
    assert resp.status_code == 200
    camp = _json(resp)["camp"]
    assert camp["slug"] == "letni-tabor" and camp["length_days"] == 5
    # no default categories — a fresh camp starts empty
    tl = _get(client, f"/api/camps/{camp['slug']}/timeline")
    assert tl["categories"] == []


def test_camp_create_validation_returns_pydantic_error_list(client):
    resp = client.post("/api/camps", json={**_NEW_CAMP, "name": ""}, headers=ADMIN)
    assert resp.status_code == 422
    body = _json(resp)
    assert isinstance(body, list) and any("name" in e["loc"] for e in body)


def test_camp_create_copies_taxonomies_from_source(client, seeded):
    resp = client.post("/api/camps", json={**_NEW_CAMP, "copy_from": seeded["slug"]}, headers=ADMIN)
    assert resp.status_code == 200
    new_slug = _json(resp)["camp"]["slug"]
    tl = _get(client, f"/api/camps/{new_slug}/timeline")
    assert {c["key"] for c in tl["categories"]} == {"hra"}  # copied from the source camp


def test_camp_create_copies_only_selected_parts(client, seeded):
    # copy only orgs -> the new camp gets no categories
    resp = client.post("/api/camps",
                      json={**_NEW_CAMP, "copy_from": seeded["slug"], "copy_parts": ["orgs"]}, headers=ADMIN)
    assert resp.status_code == 200
    new_slug = _json(resp)["camp"]["slug"]
    tl = _get(client, f"/api/camps/{new_slug}/timeline")
    assert tl["categories"] == []


def test_camp_create_unknown_copy_source(client):
    resp = client.post("/api/camps", json={**_NEW_CAMP, "copy_from": "nope"}, headers=ADMIN)
    assert resp.status_code == 400


def test_camp_create_duplicate_slug(client, seeded):
    resp = client.post("/api/camps", json={**_NEW_CAMP, "slug": seeded["slug"]}, headers=ADMIN)
    assert resp.status_code == 400 and "Slug" in _json(resp)["error"]


def test_camp_create_forbidden_for_editor(client, seeded):
    resp = client.post("/api/camps", json=_NEW_CAMP, headers=editor(seeded["slug"]))
    assert resp.status_code == 403


def test_camp_update_settings(client, seeded):
    resp = client.put(f"/api/camps/{seeded['slug']}",
                      json={**_NEW_CAMP, "name": "Tábor", "slug": "t", "length_days": 9}, headers=ADMIN)
    assert resp.status_code == 200
    assert _json(resp)["camp"]["length_days"] == 9


def test_editor_cannot_change_name(client, seeded):
    slug = seeded["slug"]
    resp = client.put(f"/api/camps/{slug}",
                      json={**_NEW_CAMP, "name": "Přejmenováno", "length_days": 4},
                      headers=editor(slug))
    assert resp.status_code == 200
    assert _json(resp)["camp"]["name"] == "Tábor"  # meta change ignored for editors


# --- taxonomy (relocated under /api) -----------------------------------------

def test_taxonomy_categories_save(client, seeded):
    url = f"/api/camps/{seeded['slug']}/categories"
    items = [{"id": seeded["cat_id"], "key": "hra", "label": "Hra", "color": "#0b8043"},
             {"key": "jidlo", "label": "Jídlo", "color": "#4285f4"}]
    resp = client.put(url, json={"items": items}, headers=ADMIN)
    assert resp.status_code == 200
    body = _json(resp)
    assert body["ok"] and len(body["items"]) == 2

    # duplicate key in the submitted list -> 400 with a specific message
    dup = [{"key": "x", "label": "A"}, {"key": "x", "label": "B"}]
    resp = client.put(url, json={"items": dup}, headers=ADMIN)
    assert resp.status_code == 400 and "opakuje" in _json(resp)["error"]


# --- reads (camps list / one camp / taxonomy) -------------------------------

def test_camp_list(client, seeded):
    camps = _get(client, "/api/camps")["camps"]
    assert seeded["slug"] in [c["slug"] for c in camps]


def test_camp_get(client, seeded):
    assert _get(client, f"/api/camps/{seeded['slug']}")["camp"]["slug"] == seeded["slug"]


def test_taxonomy_reads_per_collection(client, seeded):
    s = seeded["slug"]
    cats = _get(client, f"/api/camps/{s}/categories")["items"]
    assert {c["key"] for c in cats} == {"hra"}
    orgs = _get(client, f"/api/camps/{s}/orgs")["items"]
    assert orgs[0]["initials"] == "K"
    tags = _get(client, f"/api/camps/{s}/tags")["items"]
    assert tags[0]["name"] == "Důležité"


# --- permissions -------------------------------------------------------------

def test_anonymous_is_401_and_viewer_cannot_edit(client, seeded):
    slug = seeded["slug"]
    # anonymous (no headers) -> 401 on a read
    assert client.get(f"/api/camps/{slug}/timeline").status_code == 401

    # viewer can read but not mutate
    assert client.get(f"/api/camps/{slug}/timeline", headers=viewer(slug)).status_code == 200
    resp = client.post(f"/api/camps/{slug}/activities", json={"title": "X"}, headers=viewer(slug))
    assert resp.status_code == 403

    # editor can mutate
    resp = client.post(f"/api/camps/{slug}/activities", json={"title": "X"}, headers=editor(slug))
    assert resp.status_code == 200


def test_timeline_read_query_count_is_constant(app, client, seeded):
    slug, org_id, aid = seeded["slug"], seeded["org_id"], seeded["activity_id"]
    url = f"/api/camps/{slug}/timeline"

    def wire(activity_id):  # give an activity a garant + a slot with an attendee
        client.put(f"/api/activities/{activity_id}/orgs",
                   json={"orgs": [{"org_id": org_id, "role": "garant"}]}, headers=ADMIN)
        sid = _make_slot(client, slug, activity_id)
        client.put(f"/api/slots/{sid}/orgs", json={"org_ids": [org_id]}, headers=ADMIN)

    wire(aid)
    aid2 = _json(client.post(f"/api/camps/{slug}/activities", json={"title": "B"}, headers=ADMIN))["activity"]["id"]
    wire(aid2)
    two = _count_queries(lambda: client.get(url, headers=ADMIN))

    for i in range(3):  # grow to five activities, all fully wired
        more = _json(client.post(f"/api/camps/{slug}/activities", json={"title": f"C{i}"}, headers=ADMIN))["activity"]["id"]
        wire(more)
    five = _count_queries(lambda: client.get(url, headers=ADMIN))

    # selectin loading → query count is independent of the number of activities (no N+1)
    assert two == five


def test_unknown_activity_is_404_json(client, seeded):
    resp = client.get("/api/activities/424242", headers=ADMIN)
    assert resp.status_code == 404
    assert _json(resp)["ok"] is False


# --- camp deletion (admin-only, must be empty) -------------------------------

def test_camp_delete_admin_only_cascades_taxonomy(client, seeded):
    # a camp with taxonomy (copied) + a material but no activities still deletes (cascade)
    client.post("/api/camps", json={"name": "Prázdná", "slug": "empty", "start_date": "2026-08-01",
                                     "length_days": 2, "copy_from": seeded["slug"]}, headers=ADMIN)
    _make_material(client, "empty", name="papír")
    # non-admin (editor of the camp) is refused
    assert client.delete("/api/camps/empty", headers=editor("empty")).status_code == 403
    # admin deletes it; it's gone afterwards
    assert client.delete("/api/camps/empty", headers=ADMIN).status_code == 200
    assert client.get("/api/camps/empty", headers=ADMIN).status_code == 404


def test_camp_delete_blocked_with_activities(client, seeded):
    resp = client.delete(f"/api/camps/{seeded['slug']}", headers=ADMIN)
    assert resp.status_code == 400 and "nelze smazat" in _json(resp)["error"]


# --- catalog material update + overview --------------------------------------

def test_material_update_fields(client, seeded):
    slug = seeded["slug"]
    mid = _make_material(client, slug, name="papír", unit="ks")["material"]["id"]
    resp = client.patch(f"/api/camps/{slug}/materials/{mid}",
                        json={"name": "kancelářský papír", "unit": "balení", "note": "A4"}, headers=ADMIN)
    assert resp.status_code == 200
    m = _json(resp)["material"]
    assert (m["name"], m["unit"], m["note"]) == ("kancelářský papír", "balení", "A4")


def test_material_update_rename_collision(client, seeded):
    slug = seeded["slug"]
    _make_material(client, slug, name="papír")
    mid = _make_material(client, slug, name="lepidlo")["material"]["id"]
    resp = client.patch(f"/api/camps/{slug}/materials/{mid}", json={"name": "papír"}, headers=ADMIN)
    assert resp.status_code == 400 and "už v katalogu" in _json(resp)["error"]


def test_material_overview_lists_usages(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    mid = _make_material(client, slug, name="papír", unit="ks")["material"]["id"]
    client.post(f"/api/activities/{aid}/materials",
                json={"material_id": mid, "amount": 30, "is_ready": True}, headers=ADMIN)
    body = _get(client, f"/api/camps/{slug}/materials/overview")
    m = next(x for x in body["materials"] if x["id"] == mid)
    assert len(m["usages"]) == 1
    u = m["usages"][0]
    assert u["activity_id"] == aid and u["amount"] == 30 and u["is_ready"] is True
    assert u["activity_title"] == "Akce"


# --- camp-wide TODO overview -------------------------------------------------

def test_todo_overview_lists_all_with_activity(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    client.post(f"/api/activities/{aid}/todos", json={"title": "Koupit lano"}, headers=ADMIN)
    client.post(f"/api/activities/{aid}/todos", json={"title": "Hotovo", "is_done": True}, headers=ADMIN)
    # a second activity with its own todo
    aid2 = _json(client.post(f"/api/camps/{slug}/activities", json={"title": "Druhá"}, headers=ADMIN))["activity"]["id"]
    client.post(f"/api/activities/{aid2}/todos", json={"title": "Z druhé"}, headers=ADMIN)

    todos_out = _get(client, f"/api/camps/{slug}/todos")["todos"]
    assert len(todos_out) == 3
    titles = {t["title"]: t for t in todos_out}
    assert titles["Koupit lano"]["activity_title"] == "Akce"
    assert titles["Z druhé"]["activity_id"] == aid2
    assert titles["Hotovo"]["is_done"] is True


# --- audit log history -------------------------------------------------------

def test_audit_feed_and_activity_filter(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    client.patch(f"/api/activities/{aid}", json={"title": "Nová akce"}, headers=ADMIN)
    client.post(f"/api/camps/{slug}/activities", json={"title": "Druhá"}, headers=ADMIN)

    entries = _get(client, f"/api/camps/{slug}/audit")["entries"]
    assert len(entries) >= 2
    assert entries[0]["created_at"] >= entries[-1]["created_at"]  # newest first

    only = _get(client, f"/api/camps/{slug}/audit?activity_id={aid}")["entries"]
    assert only and all(e["activity_id"] == aid for e in only)
    upd = next(e for e in only if e["action"] == "update")
    assert upd["changes"]["title"] == ["Akce", "Nová akce"]


def test_audit_requires_view(client, seeded):
    assert client.get(f"/api/camps/{seeded['slug']}/audit").status_code == 401


def test_audit_entity_type_filter(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    client.patch(f"/api/activities/{aid}", json={"title": "Nová"}, headers=ADMIN)       # entity_type=activity
    client.post(f"/api/camps/{slug}/materials", json={"name": "papír"}, headers=ADMIN)  # entity_type=material

    mats = _get(client, f"/api/camps/{slug}/audit?entity_type=material")["entries"]
    assert mats and all(e["entity_type"] == "material" for e in mats)
    # the enum validates the filter value — an unknown one is a 422, not a silent empty list
    assert client.get(f"/api/camps/{slug}/audit?entity_type=bogus", headers=ADMIN).status_code == 422


def test_timeline_save_emits_timeline_and_per_slot_audit(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    s1 = _make_slot(client, slug, aid)  # one create
    rev = _json(client.get(f"/api/camps/{slug}/timeline", headers=ADMIN))["camp"]["rev"]
    resp = client.patch(f"/api/camps/{slug}/timeline", json={
        "rev": rev,
        "creates": [{"activity_id": aid, "role": "prep",
                     "start_at": "2026-07-04T13:30", "end_at": "2026-07-04T14:00"}],
        "moves": [{"slot_id": s1, "start_at": "2026-07-04T15:00", "end_at": "2026-07-04T17:00"}],
    }, headers=ADMIN)
    assert resp.status_code == 200

    kinds = [(e["entity_type"], e["action"]) for e in
             _json(client.get(f"/api/camps/{slug}/audit", headers=ADMIN))["entries"]]
    assert ("timeline", "update") in kinds   # one batch-level summary
    assert ("slot", "create") in kinds       # the prep slot
    assert ("slot", "update") in kinds        # the move

    # per-slot rows are grouped under the activity and carry the time diff
    slot_rows = _json(client.get(f"/api/camps/{slug}/audit?entity_type=slot", headers=ADMIN))["entries"]
    assert slot_rows and all(e["activity_id"] == aid for e in slot_rows)
    mv = next(e for e in slot_rows if e["action"] == "update")
    assert mv["changes"]["start_at"] == ["2026-07-04T14:00:00", "2026-07-04T15:00:00"]


def test_audit_pagination_walks_older_entries(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    # generate several audit rows (each title change is one entry)
    for i in range(5):
        client.patch(f"/api/activities/{aid}", json={"title": f"t{i}"}, headers=ADMIN)

    seen, before, pages = [], None, 0
    while True:
        url = f"/api/camps/{slug}/audit?limit=2" + (f"&before={before}" if before else "")
        body = _get(client, url)
        seen.extend(e["id"] for e in body["entries"])
        pages += 1
        before = body["next_before"]
        if before is None:
            break
    # ids strictly descending across pages, no overlaps, and pagination actually happened
    assert seen == sorted(set(seen), reverse=True)
    assert pages >= 3 and len(seen) >= 5
