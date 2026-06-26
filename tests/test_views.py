"""Web (main blueprint) page tests. Currently the timeline edit-mode wiring:
editors get the edit controls + the API edit-config block; viewers don't."""

from __future__ import annotations

from tests.conftest import ADMIN, editor, viewer


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
    assert f"/api/camps/{slug}/audit" in html             # change-history feed url resolves
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


def test_materials_page_renders_with_data(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    # seed one catalog material + a need on the seeded activity so a usage is embedded
    mat = client.post(f"/api/camps/{slug}/materials", json={"name": "Lano", "unit": "m"}, headers=ADMIN)
    assert mat.status_code == 200
    mid = mat.get_json()["material"]["id"]
    assert client.post(f"/api/activities/{aid}/materials",
                       json={"material_id": mid, "amount": 30}, headers=ADMIN).status_code == 200

    html = client.get(f"/camps/{slug}/materials", headers=ADMIN).get_data(as_text=True)
    assert 'id="cp-materials-data"' in html              # the embedded JSON the JS renders from
    assert 'id="cp-materials"' in html                   # the mount point
    assert "js/materials-overview.js" in html
    assert f"/api/camps/{slug}/materials/0" in html       # materialItem (PATCH/DELETE) url resolves
    assert "/api/material-needs/0" in html                # needItem url resolves
    assert '"may_edit": true' in html                     # admin can edit
    assert "Lano" in html                                 # embedded material, with its usage


def test_materials_viewer_read_only(client, seeded):
    slug = seeded["slug"]
    html = client.get(f"/camps/{slug}/materials", headers=viewer(slug)).get_data(as_text=True)
    assert '"may_edit": false' in html


def test_materials_404_for_unknown_camp(client, seeded):
    # the page is camp-scoped (no item id in the URL); a non-existent slug → 404
    assert client.get("/camps/neexistuje/materials", headers=ADMIN).status_code == 404


def test_overview_page_renders_with_data(client, seeded):
    slug = seeded["slug"]
    html = client.get(f"/camps/{slug}/activities", headers=ADMIN).get_data(as_text=True)
    assert 'id="cp-overview-data"' in html               # the embedded JSON the JS renders from
    assert 'id="cp-overview"' in html                    # the mount point
    assert "js/activities-overview.js" in html
    assert "/api/activities/0" in html                    # activityItem (DELETE) url resolves
    assert "/api/activities/0/merge" in html              # activityMerge url resolves
    assert '"may_edit": true' in html                     # admin can edit
    assert "Akce" in html                                 # the seeded activity


def test_overview_viewer_read_only(client, seeded):
    slug = seeded["slug"]
    html = client.get(f"/camps/{slug}/activities", headers=viewer(slug)).get_data(as_text=True)
    assert '"may_edit": false' in html


def test_overview_404_for_unknown_camp(client, seeded):
    # the page is camp-scoped (no item id in the URL); a non-existent slug → 404
    assert client.get("/camps/neexistuje/activities", headers=ADMIN).status_code == 404


def test_todos_page_renders_with_data(client, seeded):
    slug = seeded["slug"]
    html = client.get(f"/camps/{slug}/todos", headers=ADMIN).get_data(as_text=True)
    assert 'id="cp-todos-data"' in html                  # the embedded JSON the JS renders from
    assert 'id="cp-todos"' in html                       # the mount point
    assert "js/todo-list.js" in html                     # the shared component
    assert "js/todos-overview.js" in html
    assert "/api/todos/0" in html                         # todoItem (PATCH/DELETE) url resolves
    assert '"may_edit": true' in html                     # admin can edit


def test_todos_viewer_read_only(client, seeded):
    slug = seeded["slug"]
    html = client.get(f"/camps/{slug}/todos", headers=viewer(slug)).get_data(as_text=True)
    assert '"may_edit": false' in html


def test_todos_404_for_unknown_camp(client, seeded):
    assert client.get("/camps/neexistuje/todos", headers=ADMIN).status_code == 404


def test_camp_detail_has_history_tab(client, seeded):
    slug = seeded["slug"]
    html = client.get(f"/camps/{slug}/detail", headers=ADMIN).get_data(as_text=True)
    assert 'data-tax-tab="history"' in html              # the tab button
    assert 'data-history-root' in html                    # the feed mount
    assert 'data-history-mode' in html                    # the camp-level / full-history toggle
    assert f"/api/camps/{slug}/audit" in html             # audit url resolves into the embed
    assert "js/history-feed.js" in html


# --- camp settings: delete button ---------------------------------------------------

def test_camp_edit_delete_button_disabled_with_activities(client, seeded):
    # admin sees the delete button, but the seeded camp has an activity → disabled + tooltip
    html = client.get(f"/camps/{seeded['slug']}/edit", headers=ADMIN).get_data(as_text=True)
    assert "data-delete-camp" in html
    assert f"/api/camps/{seeded['slug']}" in html      # the DELETE url resolves
    assert "js/camp-settings.js" in html
    button = html[html.index("data-delete-camp"):html.index("</button>", html.index("data-delete-camp"))]
    assert "disabled" in button and "title=" in button


def test_camp_edit_delete_button_enabled_when_empty(client, seeded):
    # a camp with no activities → the button is present and NOT disabled
    client.post("/api/camps", json={"name": "Prázdná", "slug": "prazdna", "start_date": "2026-09-01",
                                    "length_days": 3, "timezone": "Europe/Prague",
                                    "window_start_min": 240, "snap_minutes": 15}, headers=ADMIN)
    html = client.get("/camps/prazdna/edit", headers=ADMIN).get_data(as_text=True)
    button = html[html.index("data-delete-camp"):html.index("</button>", html.index("data-delete-camp"))]
    assert "disabled" not in button


def test_camp_edit_no_delete_button_for_editor(client, seeded):
    # delete is admin-only (can_edit_camp_meta) → an editor never sees the button
    html = client.get(f"/camps/{seeded['slug']}/edit", headers=editor(seeded["slug"])).get_data(as_text=True)
    assert "data-delete-camp" not in html


# --- condensed header (brand + camp heading + account on one line) ------------------

def test_header_merges_brand_camp_heading_and_account(client, seeded):
    html = client.get(f"/camps/{seeded['slug']}", headers=ADMIN).get_data(as_text=True)
    assert 'class="cp-header"' in html
    assert 'class="cp-nav"' not in html                  # old top bar is gone
    assert "cp-brand" in html and "Camp Planner" in html  # grey brand
    assert "cp-camp-name" in html and "Tábor" in html     # camp name now lives in the header
    assert "cp-account" in html                           # name / Uživatelé / Logout on the right


def test_landing_heading_rides_in_header(client, seeded):
    html = client.get("/", headers=ADMIN).get_data(as_text=True)
    assert 'class="cp-header"' in html and "cp-brand" in html
    # "Akce" sits in the header's camp-name slot (where camp pages show the camp name)
    head = html[html.index('class="cp-header"'):html.index("</header>")]
    assert "cp-camp-name" in head and "Akce" in head


def test_landing_page_renders_camp_rows(client, seeded):
    # the camp list is one row per camp: name, date range + section links
    html = client.get("/", headers=ADMIN).get_data(as_text=True)
    assert "css/landing.css" in html
    assert "cp-camp-rows" in html and "cp-camp-row-name" in html
    assert "Tábor" in html                                # seeded camp name (3-day camp → a range)
    assert "4. 7. 2026 – 6. 7. 2026" in html              # start_date.end_date via Camp.end_date
    assert "3 dny" in html                                # Czech plural for length_days


def test_landing_page_orders_newest_first(client, seeded):
    # a later camp must appear above the earlier seeded one (ordered by start_date desc)
    client.post("/api/camps", json={"name": "Pozdější", "slug": "pozd", "start_date": "2026-09-01",
                                    "length_days": 3, "timezone": "Europe/Prague",
                                    "window_start_min": 240, "snap_minutes": 15}, headers=ADMIN)
    html = client.get("/", headers=ADMIN).get_data(as_text=True)
    assert html.index("Pozdější") < html.index("Tábor")   # newest (2026-09) before older (2026-07)
