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
