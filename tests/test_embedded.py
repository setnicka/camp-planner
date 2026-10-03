"""Embedded mode: register_camp_planner mounts the blueprints on a host Flask app
under a URL prefix, with identity supplied by the host's auth callback.
"""

from __future__ import annotations

import io
import re
import warnings

import pytest
from flask import Flask
from flask_wtf import CSRFProtect
from jinja2 import DictLoader

from camp_planner import register_camp_planner
from camp_planner.extensions import db
from tests.conftest import HOST_ADMIN, make_camp, page_data, png


@pytest.fixture
def embedded_factory():
    """Mount Camp Planner on a bare host app at /planner. The builder takes
    register_camp_planner kwargs and gives back (client, identity holder)."""
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
    """The default mount: (client, identity holder); tests set holder["value"] to the
    dict the host callback would return."""
    return embedded_factory()


def test_mounts_under_prefix_with_host_identity(embedded):
    client, holder = embedded

    # anonymous (callback returns None): the landing page renders, links carry the prefix
    html = client.get("/planner/").get_data(as_text=True)
    assert "nejste přihlášeni" in html
    assert 'href="/planner/"' in html

    holder["value"] = HOST_ADMIN
    make_camp(client, "t", prefix="/planner")

    html = client.get("/planner/camps/t").get_data(as_text=True)
    assert 'id="cp-timeline-data"' in html
    assert "/planner/api/camps/t/timeline" in html
    assert "/planner/camps/t/activities/0" in html


def test_slug_grants_scope_access(embedded):
    client, holder = embedded
    holder["value"] = HOST_ADMIN
    make_camp(client, "t", prefix="/planner")

    holder["value"] = {"user_id": "host-user",
                       "grants": [{"role": "viewer", "camps": ["t"]}]}
    assert client.get("/planner/camps/t").status_code == 200
    assert client.patch("/planner/api/camps/t", json={"length_days": 4}).status_code == 403

    holder["value"] = {"user_id": "host-user",
                       "grants": [{"role": "viewer", "camps": ["jina"]}]}
    assert client.get("/planner/camps/t").status_code == 403


def test_malformed_grant_is_skipped_not_500(embedded):
    client, holder = embedded
    holder["value"] = {"user_id": "host-user",
                       "grants": [{"rolle": "typo"}, "nonsense"]}
    assert client.get("/planner/").status_code == 200


HOST_BASE = """<html><head><title>Host</title>
<link rel=stylesheet href="/planner/static/css/content.css">
{% block cp_head %}{% endblock %}</head>
<body><nav>{% block cp_nav %}{% endblock %}</nav>
{% block content %}{% endblock %}{% block cp_scripts %}{% endblock %}</body></html>"""


@pytest.mark.parametrize("host_base, theme, wrapper, switch", [
    (False, "dark", '<div class="cp-embed" data-cp-theme="dark">', False),
    # for a host page that itself follows prefers-color-scheme
    (False, "auto", '<div class="cp-embed" data-cp-theme="auto">', False),
    # unforced, the visitor chooses; no "auto" either, as the host's page decides, not the OS
    (False, None, '<div class="cp-embed">', True),
    (True, "dark", '<div class="cp-embed" data-cp-theme="dark">', False),
    (True, None, '<div class="cp-embed">', False),
])
def test_the_theme_rides_on_our_wrapper(embedded_factory, host_base, theme, wrapper, switch):
    """content.css keys the theme off our wrapper, as <html> is the host's. The wrapper
    ships unforced too: it carries the palette's background and text."""
    host = {"base_template": "hb.html", "templates": {"hb.html": HOST_BASE}} if host_base else {}
    client, _ = embedded_factory(force_theme=theme, **host)
    html = client.get("/planner/").get_data(as_text=True)
    assert wrapper in html
    assert html.count('data-cp-theme="') == wrapper.count('data-cp-theme="')   # nothing else
    assert ("data-cp-theme-switch" in html) is switch
    assert ("/planner/static/js/theme.js" in html) is switch
    if host_base:   # the host's shell rendered; our bare.html with its nav did not
        assert "<title>Host</title>" in html and "cp-camp-nav" not in html
    elif theme:     # the wrapper closes our markup; only the deferred header script follows
        assert html.split("<script")[0].rstrip().endswith("</div>")


def _asset_name(url):   # the file name, the ?v= cache buster dropped
    return url.split("?", 1)[0].rsplit("/", 1)[-1]


def _assets(html):
    return ([_asset_name(u) for u in re.findall(r'<link[^>]*href="([^"]+)"', html)],
            [_asset_name(u) for u in re.findall(r'<script[^>]*src="([^"]+)"', html)])


def test_embedded_pages_ship_their_own_css_js_and_csrf_token(embedded):
    """Only full.html has a <head>/end-of-body, so page.html forwards each page's assets
    inline when embedded."""
    client, holder = embedded
    holder["value"] = HOST_ADMIN
    make_camp(client, "t", prefix="/planner")
    html = client.get("/planner/camps/t").get_data(as_text=True)

    css, js = _assets(html)
    assert "content.css" in css        # the palette, so a host needn't link it
    assert "timeline.css" in css and "components.css" in css
    assert "vis-timeline-graph2d.min.js" in js and "timeline.js" in js
    assert 'name="csrf-token"' in html
    # content.css first: page CSS reads the tokens it defines
    assert css.index("content.css") < css.index("timeline.css")


def test_a_host_base_template_places_our_assets_where_it_wants(embedded_factory):
    """The contract is four slots, and only the host's template can place our stylesheets
    in its <head> and our scripts before </body>."""
    client, holder = embedded_factory(base_template="hb.html", templates={"hb.html": HOST_BASE})
    holder["value"] = HOST_ADMIN
    make_camp(client, "t", prefix="/planner")
    html = client.get("/planner/camps/t").get_data(as_text=True)

    head, body = html.split("</head>", 1)
    assert "timeline.css" in head and "components.css" in head
    assert "timeline.js" not in head and "timeline.js" in body
    assert 'name="csrf-token"' in head                            # csrf_meta rides in cp_head
    assert "cp-timeline-data" in body


def test_a_host_base_template_missing_the_slots_warns_at_registration(embedded_factory):
    """At startup, instead of shipping unstyled, inert pages."""
    with pytest.warns(RuntimeWarning, match="cp_head / cp_scripts / cp_nav"):
        embedded_factory(base_template="bad.html",
                         templates={"bad.html": "<html><body>{% block content %}{% endblock %}"
                                                "</body></html>"})


def test_a_host_template_that_extends_another_is_not_second_guessed(embedded_factory):
    """The check reads one file and can't see inherited slots, so it stays quiet rather
    than cry wolf."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        embedded_factory(base_template="child.html", templates={
            "root.html": "<html><head>{% block cp_head %}{% endblock %}</head><body>"
                         "{% block content %}{% endblock %}{% block cp_scripts %}{% endblock %}"
                         "</body></html>",
            "child.html": '{% extends "root.html" %}',
        })
    assert not [c for c in caught if c.category is RuntimeWarning]


def test_the_theme_does_not_land_in_the_hosts_config(embedded_factory):
    """app.config belongs to the host; our resolved value lives in our extensions state."""
    app = embedded_factory(force_theme="dark")[0].application
    assert "CP_FORCE_THEME" not in app.config
    assert app.extensions["camp_planner"]["force_theme"] == "dark"


def test_a_bad_theme_value_fails_loudly(embedded_factory):
    with pytest.raises(ValueError, match="expected 'light', 'dark', 'auto' or None"):
        embedded_factory(force_theme="midnight")


def test_embedded_host_switches_it_off(embedded_factory):
    client, holder = embedded_factory(disabled_features=["inventory"])
    holder["value"] = HOST_ADMIN
    assert client.get("/planner/inventory").status_code == 404
    assert client.get("/planner/api/inventory/items").status_code == 404
    with pytest.raises(ValueError):
        embedded_factory(disabled_features=["inventroy"])


def test_the_warehouse_under_a_host(embedded_factory, tmp_path):
    """Photos come from media_dir= and every url carries the prefix."""
    client, holder = embedded_factory(media_dir=str(tmp_path))
    holder["value"] = HOST_ADMIN
    box = client.post("/planner/api/inventory/boxes", json={"name": "B"}).get_json()["box"]
    item = client.post("/planner/api/inventory/items",
                       json={"name": "Lano", "box_id": box["id"]}).get_json()["item"]

    data = page_data(client, f"/planner/inventory/boxes/{box['id']}")
    assert data["photos_enabled"] is True
    assert all(url.startswith("/planner/") for url in data["urls"].values())

    resp = client.post(f"/planner/api/inventory/items/{item['id']}/photos",
                       data={"photos": (io.BytesIO(png()), "foto.png")},
                       content_type="multipart/form-data")
    photo = resp.get_json()["item"]["photos"][0]
    assert list((tmp_path / "inventory").rglob(photo["filename"]))
    assert client.get(f"/planner/inventory/photos/thumb/{photo['filename']}").status_code == 200
