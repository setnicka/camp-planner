"""Web (main blueprint) page tests: the embedded JSON renders with resolved URLs, and the edit
affordances follow the viewer's permissions. The serializers behind it: test_serialize.py."""

from __future__ import annotations

import re

import pytest

from camp_planner import create_app
from camp_planner.extensions import db
from tests.conftest import ADMIN, editor, make_camp, make_material, ok, page_data, viewer


def test_static_urls_carry_a_version(client, seeded):
    html = client.get(f"/camps/{seeded['slug']}", headers=ADMIN).get_data(as_text=True)
    assert re.search(r'/static/js/timeline\.js\?v=\d+"', html)
    assert re.search(r'/static/css/timeline\.css\?v=\d+"', html)


def test_timeline_page_edit_wiring_for_editor(client, seeded):
    slug = seeded["slug"]
    html = client.get(f"/camps/{slug}", headers=ADMIN).get_data(as_text=True)
    assert 'id="cp-edit-toggle"' in html
    assert 'id="cp-timeline-edit"' in html          # the edit-config JSON block
    assert f"/api/camps/{slug}/timeline" in html     # save url
    assert f"/api/camps/{slug}/activities" in html   # picker url
    assert 'name="csrf-token"' in html
    assert 'class="cp-help-edit"' in html


def test_timeline_page_read_only_for_viewer(client, seeded):
    slug = seeded["slug"]
    html = client.get(f"/camps/{slug}", headers=viewer(slug)).get_data(as_text=True)
    assert 'id="cp-edit-toggle"' not in html
    assert 'id="cp-timeline-edit"' not in html
    # viewers still get the way into the activity detail (slot select → Detail button)
    assert f'data-activity-detail="/camps/{slug}/activities/0"' in html
    assert 'class="cp-help-view"' in html
    assert 'class="cp-help-edit"' not in html


# --- section pages -------------------------------------------------------------------

PAGES = [  # path under the camp, inline JSON, scripts, url keys the script calls
    ("/activities/{aid}", "cp-activity-data", ["js/activity-detail.js"],
     {"orgs": "/api/activities/{aid}/orgs", "audit": "/api/camps/t/audit"}),
    ("/materials", "cp-materials-data", ["js/materials-overview.js"],
     {"materialItem": "/api/camps/t/materials/0", "needItem": "/api/material-needs/0"}),
    ("/activities", "cp-overview-data", ["js/activities-overview.js"],
     {"activityItem": "/api/activities/0", "activityMerge": "/api/activities/0/merge"}),
    ("/todos", "cp-todos-data", ["js/todos-overview.js", "js/todo-list.js"],
     {"todoItem": "/api/todos/0"}),
]


@pytest.mark.parametrize("path, script_id, scripts, urls", PAGES)
def test_section_page(client, seeded, path, script_id, scripts, urls):
    aid = seeded["activity_id"]
    url = "/camps/t" + path.format(aid=aid)
    html = client.get(url, headers=ADMIN).get_data(as_text=True)
    assert all(script in html for script in scripts)
    data = page_data(client, url, script=script_id)
    for key, expected in urls.items():
        assert data["urls"][key] == expected.format(aid=aid)
    assert data["may_edit"] is True
    assert page_data(client, url, viewer("t"), script=script_id)["may_edit"] is False


@pytest.mark.parametrize("url", [
    "/camps/neexistuje/activities", "/camps/neexistuje/materials", "/camps/neexistuje/todos",
    "/camps/jina/activities/{aid}",   # the activity exists, under another camp: no leak
])
def test_unknown_camp_is_404(client, seeded, url):
    make_camp(client, "jina")
    assert client.get(url.format(aid=seeded["activity_id"]), headers=ADMIN).status_code == 404


def test_overview_pages_embed_what_their_scripts_render(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    mid = make_material(client, slug, "Lano", unit="m")["id"]
    ok(client.post(f"/api/activities/{aid}/materials",
                   json={"material_id": mid, "amount": 30}, headers=ADMIN))

    materials = page_data(client, f"/camps/{slug}/materials", script="cp-materials-data")
    assert [(m["name"], len(m["usages"])) for m in materials["materials"]] == [("Lano", 1)]
    assert [o["name"] for o in materials["orgs"]] == ["Karel"]   # the edit modal's org picker

    overview = page_data(client, f"/camps/{slug}/activities", script="cp-overview-data")
    assert [(a["title"], len(a["slots"])) for a in overview["activities"]] == [("Akce", 0)]
    # the day window the chronological sort groups slots by
    assert overview["camp"] == {"start_date": "2026-07-04", "length_days": 3,
                                "window_start_min": 240}


# --- camp detail --------------------------------------------------------------------

def test_camp_detail_has_history_tab(client, seeded):
    slug = seeded["slug"]
    html = client.get(f"/camps/{slug}/detail", headers=ADMIN).get_data(as_text=True)
    assert 'data-tax-tab="history"' in html
    assert 'data-history-root' in html
    assert 'data-history-mode' in html                    # the camp-level / full-history toggle
    assert f"/api/camps/{slug}/audit" in html
    assert "js/history-feed.js" in html


def test_camp_detail_token_tab_for_editor(client, seeded):
    slug = seeded["slug"]
    # a token is embedded so the list-with-data path renders too
    client.post(f"/api/camps/{slug}/tokens", json={"name": "sync", "role": "editor"}, headers=ADMIN)
    html = client.get(f"/camps/{slug}/detail", headers=editor(slug)).get_data(as_text=True)
    assert 'data-tax-tab="tokens"' in html
    assert 'data-tokens-root' in html
    assert 'id="cp-tokens-data"' in html
    assert "js/token-admin.js" in html
    assert f"/api/camps/{slug}/tokens" in html
    assert "/api/tokens/0" in html                        # revoke url (0 sentinel)
    assert '"sync"' in html and '"created_by"' in html    # the embedded token, without its secret
    assert "token_hash" not in html


def test_camp_detail_token_tab_hidden_from_viewer(client, seeded):
    slug = seeded["slug"]
    html = client.get(f"/camps/{slug}/detail", headers=viewer(slug)).get_data(as_text=True)
    assert 'data-tax-tab="tokens"' not in html
    assert 'id="cp-tokens-data"' not in html
    assert "js/token-admin.js" not in html


# --- camp settings -------------------------------------------------------------------

def _delete_button(html):
    start = html.index("data-delete-camp")
    return html[start:html.index("</button>", start)]


def test_camp_edit_delete_button(client, seeded):
    """Admin-only (can_edit_camp_meta), and disabled while the camp has activities, with
    the reason as visible text rather than a hover-only tooltip."""
    html = client.get("/camps/t/edit", headers=ADMIN).get_data(as_text=True)
    assert "/api/camps/t" in html and "js/camp-settings.js" in html
    assert "disabled" in _delete_button(html)
    assert "Akci nelze smazat, dokud má aktivity" in html

    make_camp(client, "prazdna")
    empty = client.get("/camps/prazdna/edit", headers=ADMIN).get_data(as_text=True)
    assert "disabled" not in _delete_button(empty)

    as_editor = client.get("/camps/t/edit", headers=editor("t")).get_data(as_text=True)
    assert "data-delete-camp" not in as_editor


def test_camp_edit_form_time_input_roundtrip(client, seeded):
    # "Začátek dne" is an <input type="time">: HH:MM in the form, minutes past midnight stored
    slug = seeded["slug"]
    html = client.get(f"/camps/{slug}/edit", headers=ADMIN).get_data(as_text=True)
    assert 'type="time" name="window_start_min" value="04:00"' in html

    resp = client.post(f"/camps/{slug}/edit", data={
        "name": "Tábor", "slug": slug, "start_date": "2026-07-04", "length_days": "3",
        "timezone": "Europe/Prague", "window_start_min": "06:30", "snap_minutes": "15",
    }, headers=ADMIN)
    assert resp.status_code == 302
    camp = client.get(f"/api/camps/{slug}", headers=ADMIN).get_json()["camp"]
    assert camp["window_start_min"] == 390


# --- header -------------------------------------------------------------------------

def test_header_carries_heading_links_and_menu(client, seeded):
    """The bar carries the heading and the section links; account and theme hang in the
    menu on every screen width."""
    def header(url):
        html = client.get(url, headers=ADMIN).get_data(as_text=True)
        return html[html.index('class="cp-header"'):html.index("</header>")]

    head = header("/camps/t")
    assert "cp-brand" in head and "Camp Planner" in head
    assert "cp-camp-name" in head and "Tábor" in head
    assert "cp-camp-links" in head
    pop = head[head.index("cp-account-pop"):]
    assert "cp-account-menu" in head and "data-cp-theme-switch" in pop
    assert head.count("data-cp-theme-switch") == 1

    # the landing page's "Akce" sits where camp pages show the camp name
    landing = header("/")
    assert "cp-brand" in landing and "cp-camp-name" in landing and "Akce" in landing


def test_page_carries_csrf_refresh_meta(client, seeded):
    # a long-open page renews an expired token from this endpoint (prefix-safe via url_for)
    html = client.get(f"/camps/{seeded['slug']}", headers=ADMIN).get_data(as_text=True)
    assert 'name="csrf-token"' in html
    url = re.search(r'name="csrf-refresh" content="([^"]+)"', html).group(1)
    assert url.endswith("/csrf-token")


# --- landing ------------------------------------------------------------------------

def test_landing_page_renders_camp_rows(client, seeded):
    html = client.get("/", headers=ADMIN).get_data(as_text=True)
    assert "css/landing.css" in html
    assert "cp-camp-rows" in html and "cp-camp-row-name" in html
    assert "Úkoly" in html                                # row links come from the shared sections list
    assert "Tábor" in html
    assert "4. 7. 2026 – 6. 7. 2026" in html              # start_date.end_date via Camp.end_date
    assert "3 dny" in html                                # Czech plural for length_days


def test_landing_page_orders_newest_first(client, seeded):
    make_camp(client, "pozd", name="Pozdější")
    html = client.get("/", headers=ADMIN).get_data(as_text=True)
    assert html.index("Pozdější") < html.index("Tábor")   # 2026-09 before 2026-07


# --- colour theme -----------------------------------------------------------

def test_theme_switch_is_rendered_and_follows_the_os(client):
    """Default deployment: the visitor chooses, so the switch and its script ship. The
    shell opts into prefers-color-scheme with "auto": it owns the page background."""
    html = client.get("/").get_data(as_text=True)
    assert '<html lang="cs" data-cp-theme="auto">' in html
    assert "data-cp-theme-switch" in html
    assert 'data-theme="light"' in html and 'data-theme="auto"' in html and 'data-theme="dark"' in html
    assert "cp-pill-knob" in html
    assert "js/theme.js" in html
    # the pre-paint re-apply script, so a reload of a dark page doesn't flash white
    assert 'localStorage.getItem("cp-theme")' in html
    # this shell paints <body> itself, so page.html adds no wrapper of its own
    assert "cp-embed" not in html


def test_pinned_theme_replaces_the_switch(monkeypatch):
    """CP_FORCE_THEME is read at wire time, hence a fresh app rather than poking config
    on a live one."""
    from camp_planner.config import TestingConfig

    monkeypatch.setattr(TestingConfig, "CP_FORCE_THEME", "dark", raising=False)
    pinned = create_app("testing")
    with pinned.app_context():
        db.create_all()          # the index lists camps
        html = pinned.test_client().get("/").get_data(as_text=True)
        db.session.remove()      # don't leave a session bound for the next test
    assert '<html lang="cs" data-cp-theme="dark">' in html
    assert "data-cp-theme-switch" not in html
    assert "js/theme.js" not in html
    assert 'localStorage.getItem("cp-theme")' not in html
