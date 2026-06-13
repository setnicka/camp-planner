"""Web (main blueprint) page tests. Currently the timeline edit-mode wiring:
editors get the edit controls + the API edit-config block; viewers don't."""

from __future__ import annotations

from tests.conftest import ADMIN, viewer


def test_timeline_page_edit_wiring_for_editor(client, seeded):
    slug = seeded["slug"]
    html = client.get(f"/camps/{slug}", headers=ADMIN).get_data(as_text=True)
    assert 'id="cp-edit-toggle"' in html
    assert 'id="cp-timeline-edit"' in html          # the edit-config JSON block
    assert f"/api/camps/{slug}/timeline" in html     # save url resolves
    assert f"/api/camps/{slug}/activities" in html   # picker url resolves
    assert 'name="csrf-token"' in html               # needed by the PATCH/POST headers


def test_timeline_page_read_only_for_viewer(client, seeded):
    slug = seeded["slug"]
    html = client.get(f"/camps/{slug}", headers=viewer(slug)).get_data(as_text=True)
    assert 'id="cp-edit-toggle"' not in html
    assert 'id="cp-timeline-edit"' not in html


def test_activity_detail_page_renders_with_data(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    html = client.get(f"/camps/{slug}/activities/{aid}", headers=ADMIN).get_data(as_text=True)
    assert 'id="cp-activity-data"' in html              # the embedded JSON the JS renders from
    assert 'id="cp-activity"' in html                   # the mount point
    assert "js/activity-detail.js" in html
    assert f"/api/activities/{aid}/orgs" in html         # an edit url resolves
    assert '"may_edit": true' in html                    # admin can edit


def test_activity_detail_viewer_cannot_edit(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    html = client.get(f"/camps/{slug}/activities/{aid}", headers=viewer(slug)).get_data(as_text=True)
    assert '"may_edit": false' in html


def test_activity_detail_404_for_foreign_camp(client, seeded):
    # the activity exists, but not under this (other) camp's slug → 404, no cross-camp leak
    aid = seeded["activity_id"]
    other = client.post("/api/camps", json={"name": "Jiná", "slug": "jina", "start_date": "2026-08-01",
                                            "length_days": 3, "timezone": "Europe/Prague",
                                            "window_start_min": 240, "snap_minutes": 15}, headers=ADMIN)
    assert other.status_code == 200
    assert client.get(f"/camps/jina/activities/{aid}", headers=ADMIN).status_code == 404
