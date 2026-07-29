"""Embedded mode: register_camp_planner mounts the blueprints on a host Flask app
under a URL prefix, with identity supplied by the host's auth callback.
"""

from __future__ import annotations

import re

import pytest
from flask import Flask
from flask_wtf import CSRFProtect
from jinja2 import DictLoader

from camp_planner import register_camp_planner
from camp_planner.extensions import db


@pytest.fixture
def embedded_factory():
    """Mount Camp Planner on a bare host app at /planner. Returns a builder taking
    register_camp_planner kwargs (so a test can vary e.g. force_theme=) and giving back
    (client, identity holder); contexts are unwound at teardown."""
    contexts = []

    def build(templates=None, **kwargs):
        holder: dict = {"value": None}
        host = Flask(__name__)
        host.config.update(SECRET_KEY="test", TESTING=True, WTF_CSRF_ENABLED=False)
        CSRFProtect(host)   # the host owns CSRF; our templates call its csrf_token()
        if templates:
            # before registering: that's when we validate a host's base_template
            host.jinja_loader = DictLoader(templates)
        register_camp_planner(host, auth_callback=lambda: holder["value"],
                              url_prefix="/planner",
                              database_uri="sqlite:///:memory:", **kwargs)
        ctx = host.app_context()
        ctx.push()
        contexts.append(ctx)
        db.create_all()
        return host.test_client(), holder

    yield build
    for ctx in reversed(contexts):
        db.session.remove()
        ctx.pop()


@pytest.fixture
def embedded(embedded_factory):
    """The default mount: yields (client, identity holder) — tests set
    holder["value"] to the dict the host callback would return."""
    return embedded_factory()


@pytest.fixture
def embedded_dark(embedded_factory):
    """A host that pins the dark theme (the KSP case): yields just the client."""
    client, _ = embedded_factory(force_theme="dark")
    return client


def _admin(holder):
    holder["value"] = {"user_id": "host-admin", "display_name": "Host Admin", "is_admin": True}


def _make_camp(client):
    resp = client.post("/planner/api/camps", json={
        "name": "Tábor", "slug": "t", "start_date": "2026-07-04", "length_days": 3,
        "timezone": "Europe/Prague", "window_start_min": 240, "snap_minutes": 15})
    assert resp.status_code == 200


def test_mounts_under_prefix_with_host_identity(embedded):
    client, holder = embedded

    # anonymous (callback returns None): the landing page renders, links carry the prefix
    html = client.get("/planner/").get_data(as_text=True)
    assert "nejste přihlášeni" in html
    assert 'href="/planner/"' in html                    # url_for is prefix-safe

    # the host's admin identity drives the API mounted under the prefix
    _admin(holder)
    _make_camp(client)

    html = client.get("/planner/camps/t").get_data(as_text=True)
    assert 'id="cp-timeline-data"' in html
    assert "/planner/api/camps/t/timeline" in html       # embedded api urls carry the prefix
    assert "/planner/camps/t/activities/0" in html        # page urls too


def test_slug_grants_scope_access(embedded):
    client, holder = embedded
    _admin(holder)
    _make_camp(client)

    # a viewer grant by camp slug: read yes, write no
    holder["value"] = {"user_id": "host-user",
                       "grants": [{"role": "viewer", "camps": ["t"]}]}
    assert client.get("/planner/camps/t").status_code == 200
    assert client.patch("/planner/api/camps/t", json={"length_days": 4}).status_code == 403

    # no grant for the camp → no access
    holder["value"] = {"user_id": "host-user",
                       "grants": [{"role": "viewer", "camps": ["jina"]}]}
    assert client.get("/planner/camps/t").status_code == 403


def test_malformed_grant_is_skipped_not_500(embedded):
    client, holder = embedded
    holder["value"] = {"user_id": "host-user",
                       "grants": [{"rolle": "typo"}, "nonsense"]}
    resp = client.get("/planner/")
    assert resp.status_code == 200    # the bad grant is logged + skipped, not a 500


@pytest.mark.parametrize("theme", ["dark", "auto"])
def test_forced_theme_wraps_our_output_and_drops_the_switch(embedded_factory, theme):
    """The wrapper (not <html>, which is the host's) is what content.css keys the theme off;
    "auto" is for a host page that itself follows prefers-color-scheme."""
    client, _ = embedded_factory(force_theme=theme)
    html = client.get("/planner/").get_data(as_text=True)
    assert f'<div class="cp-embed" data-cp-theme="{theme}">' in html
    assert html.rstrip().endswith("</div>")
    assert "data-cp-theme-switch" not in html
    assert "js/theme.js" not in html


def test_switch_ships_when_the_host_forces_nothing(embedded):
    """Unforced, the visitor chooses — but no attribute is emitted, in particular not
    "auto": embedded, the host's page decides, not the OS."""
    client, holder = embedded
    html = client.get("/planner/").get_data(as_text=True)
    assert "data-cp-theme-switch" in html
    assert "/planner/static/js/theme.js" in html      # prefix-safe
    assert 'data-cp-theme="' not in html
    # the wrapper ships either way — it carries the palette's own background/text
    assert '<div class="cp-embed">' in html


def _assets(html):
    return ([u.rsplit("/", 1)[-1] for u in re.findall(r'<link[^>]*href="([^"]+)"', html)],
            [u.rsplit("/", 1)[-1] for u in re.findall(r'<script[^>]*src="([^"]+)"', html)])


def test_embedded_pages_ship_their_own_css_js_and_csrf_token(embedded):
    """Only full.html has a <head>/end-of-body, so page.html forwards each page's assets
    inline when embedded."""
    client, holder = embedded
    _admin(holder)
    _make_camp(client)
    html = client.get("/planner/camps/t").get_data(as_text=True)

    css, js = _assets(html)
    assert "content.css" in css        # the palette, so a host needn't link it
    assert "timeline.css" in css and "components.css" in css
    assert "vis-timeline-graph2d.min.js" in js and "timeline.js" in js
    assert 'name="csrf-token"' in html
    # content.css first: page CSS reads the tokens it defines
    assert css.index("content.css") < css.index("timeline.css")


HOST_BASE = """<html><head><title>Host</title>
<link rel=stylesheet href="/planner/static/css/content.css">
{% block cp_head %}{% endblock %}</head>
<body>{% block content %}{% endblock %}{% block cp_scripts %}{% endblock %}</body></html>"""


def test_a_host_base_template_places_our_assets_where_it_wants(embedded_factory):
    """The contract is three slots. A host declaring them gets our stylesheets in its <head> and
    our scripts before </body> — proper placement, which only the host's template can do."""
    client, holder = embedded_factory(base_template="hb.html", templates={"hb.html": HOST_BASE})
    _admin(holder)
    _make_camp(client)
    html = client.get("/planner/camps/t").get_data(as_text=True)

    head, body = html.split("</head>", 1)
    assert "timeline.css" in head and "components.css" in head   # stylesheets in <head>
    assert "timeline.js" not in head and "timeline.js" in body    # scripts at the end
    assert 'name="csrf-token"' in head                            # csrf_meta rides in cp_head
    assert "cp-timeline-data" in body


def test_a_host_base_template_missing_the_slots_warns_at_registration(embedded_factory):
    """A missing slot warns at startup instead of shipping unstyled, inert pages."""
    with pytest.warns(RuntimeWarning, match="cp_head / cp_scripts"):
        embedded_factory(base_template="bad.html",
                         templates={"bad.html": "<html><body>{% block content %}{% endblock %}"
                                                "</body></html>"})


def test_a_host_template_that_extends_another_is_not_second_guessed(embedded_factory):
    """The check reads one file, so it can't see inherited slots — it stays quiet rather than
    cry wolf at a host whose own base declares them further up."""
    import warnings as w

    with w.catch_warnings(record=True) as caught:
        w.simplefilter("always")
        embedded_factory(base_template="child.html", templates={
            "root.html": "<html><head>{% block cp_head %}{% endblock %}</head><body>"
                         "{% block content %}{% endblock %}{% block cp_scripts %}{% endblock %}"
                         "</body></html>",
            "child.html": '{% extends "root.html" %}',
        })
    assert not [c for c in caught if c.category is RuntimeWarning]


def test_force_theme_reaches_a_host_supplied_base_template(embedded_factory):
    """With a custom base_template our own shells never render, so _layouts/page.html
    emits the themed wrapper instead."""
    client, _ = embedded_factory(base_template="hb.html", templates={"hb.html": HOST_BASE},
                                 force_theme="dark")
    html = client.get("/planner/").get_data(as_text=True)
    assert "<title>Host</title>" in html                            # the host's shell rendered
    assert '<div class="cp-embed" data-cp-theme="dark">' in html     # ...and carries our theme
    assert "cp-camp-nav" not in html          # our nav lives in bare.html, which didn't render


def test_host_base_template_without_a_forced_theme_still_gets_the_wrapper(embedded_factory):
    """The wrapper carries the palette's background/text, so it ships either way — just
    without an attribute, leaving the light default."""
    client, _ = embedded_factory(base_template="hb.html", templates={"hb.html": HOST_BASE})
    html = client.get("/planner/").get_data(as_text=True)
    assert '<div class="cp-embed">' in html
    assert 'data-cp-theme="' not in html


def test_the_theme_does_not_land_in_the_hosts_config(embedded_dark):
    """app.config belongs to the host; our resolved value lives in our extensions state."""
    app = embedded_dark.application
    assert "CP_FORCE_THEME" not in app.config
    assert app.extensions["camp_planner"]["force_theme"] == "dark"


def test_a_bad_theme_value_fails_loudly(embedded_factory):
    with pytest.raises(ValueError, match="expected 'light', 'dark', 'auto' or None"):
        embedded_factory(force_theme="midnight")

