"""Activities.

The pages render an activity's relationship lists as-is, so their order is part of the
contract: unordered, the database may hand back a different row order after an edit and
silently reshuffle the UI.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from camp_planner.extensions import db
from camp_planner.models.activity import Activity, ActivityAssignment, ActivityTag, OrgRole
from camp_planner.models.camp import Tag, TagKind
from camp_planner.models.material import Material, MaterialNeed
from camp_planner.models.org import Org
from camp_planner.models.slot import Slot, SlotRole
from camp_planner.services import serialize
from tests.conftest import (
    ADMIN,
    audit,
    get_json,
    make_activity,
    make_camp,
    make_material,
    make_slot,
    ok,
)


def _delete_slot(client, slug, slot_id):
    rev = get_json(client, f"/api/camps/{slug}/timeline")["camp"]["rev"]
    ok(client.patch(f"/api/camps/{slug}/timeline", json={"rev": rev, "deletes": [slot_id]},
                    headers=ADMIN))


# --- activities --------------------------------------------------------------

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


def test_unknown_activity_is_404_json(client, seeded):
    resp = client.get("/api/activities/424242", headers=ADMIN)
    assert resp.status_code == 404
    assert resp.get_json()["ok"] is False


def test_activity_overview_serializes_slots(app, seeded):
    """The overview derives per-role counts and the chronological rows from these client-side."""
    a = db.session.get(Activity, seeded["activity_id"])
    db.session.add_all([   # main + prep slots added out of order; Activity.slots time-orders them
        Slot(activity_id=a.id, role=SlotRole.main, override_name="Odpolední",
             start_at=datetime(2026, 7, 5, 14, 0), end_at=datetime(2026, 7, 5, 15, 0)),
        Slot(activity_id=a.id, role=SlotRole.main,
             start_at=datetime(2026, 7, 5, 9, 0), end_at=datetime(2026, 7, 5, 10, 0)),
        Slot(activity_id=a.id, role=SlotRole.prep, override_name="Příprava",
             start_at=datetime(2026, 7, 5, 8, 0), end_at=datetime(2026, 7, 5, 8, 30)),
    ])
    db.session.commit()

    out = serialize.activity_overview(a)
    assert [(s["role"], s["start_at"]) for s in out["slots"]] == [
        ("prep", "2026-07-05T08:00:00"),
        ("main", "2026-07-05T09:00:00"),
        ("main", "2026-07-05T14:00:00"),
    ]
    main = [s for s in out["slots"] if s["role"] == "main"]
    assert main[0]["override_name"] is None
    assert main[1]["override_name"] == "Odpolední"
    assert main[1]["end_at"] == "2026-07-05T15:00:00"


def test_activity_slots_relationship_is_time_ordered(app, seeded):
    """The same keys as the timeline's `order` comparator, so every consumer stacks
    overlapping slots alike."""
    a = db.session.get(Activity, seeded["activity_id"])
    db.session.add_all([   # written out of order
        Slot(activity_id=a.id, role=SlotRole.main,
             start_at=datetime(2026, 7, 5, s), end_at=datetime(2026, 7, 5, e))
        for s, e in [(20, 21), (8, 9), (14, 15), (8, 12)]
    ])
    db.session.commit()
    db.session.expire_all()   # force a reload, so this reads the relationship's ORDER BY

    a = db.session.get(Activity, seeded["activity_id"])
    # 08:00–12:00 before 08:00–09:00: equal starts put the longer slot first (bottom lane)
    assert [(s.start_at.hour, s.end_at.hour) for s in a.slots] == [(8, 12), (8, 9), (14, 15), (20, 21)]


def test_activity_lists_are_deterministically_ordered(app, seeded):
    """serialize.activity() sorts what carries no SQL order: orgs and materials Czech-collated
    (Č next to C, not after Z), tags by the curated Tag.sort_order."""
    camp_id, a_id = seeded["camp_id"], seeded["activity_id"]
    orgs = [Org(camp_id=camp_id, name=n, initials=n[0]) for n in ("Dana", "Čeněk", "Adam")]
    tags = [Tag(camp_id=camp_id, name=n, kind=TagKind.label, sort_order=o)
            for n, o in [("třetí", 3), ("druhý", 2)]]          # linked in reverse below
    mats = [Material(camp_id=camp_id, name=n) for n in ("Žula", "Čep", "Deska")]
    db.session.add_all(orgs + tags + mats)
    db.session.flush()
    db.session.add_all(
        [ActivityAssignment(activity_id=a_id, org_id=o.id, role=OrgRole.garant) for o in orgs]
        + [ActivityTag(activity_id=a_id, tag_id=t.id) for t in tags]
        + [MaterialNeed(activity_id=a_id, material_id=m.id, amount=1) for m in mats])
    db.session.commit()
    db.session.expire_all()

    out = serialize.activity(db.session.get(Activity, a_id))
    assert [o["initials"] for o in out["orgs"]] == ["A", "Č", "D"]
    assert [t["name"] for t in out["tags"]] == ["druhý", "třetí"]
    assert [n["material"]["name"] for n in out["material_needs"]] == ["Čep", "Deska", "Žula"]


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
