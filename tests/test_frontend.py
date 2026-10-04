"""Page chrome and the section pages: header, theme, CSRF meta, static URLs, inline data."""

from __future__ import annotations

import re

import pytest

from camp_planner import create_app
from camp_planner.extensions import db
from tests.conftest import (
    ADMIN,
    make_camp,
    make_material,
    ok,
    page_data,
    viewer,
)


def test_static_urls_carry_a_version(client, seeded):
    html = client.get(f"/camps/{seeded['slug']}", headers=ADMIN).get_data(as_text=True)
    assert re.search(r'/static/js/timeline\.js\?v=\d+"', html)
    assert re.search(r'/static/css/timeline\.css\?v=\d+"', html)


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
    assert f'id="{script_id.removesuffix("-data")}"' in html   # the mount the script fills
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
