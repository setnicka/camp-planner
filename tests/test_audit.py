from __future__ import annotations

import pytest

from tests.conftest import (
    ADMIN,
    audit,
    get_json,
    make_activity,
    make_material,
    ok,
)


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
