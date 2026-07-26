"""Embedded mode: register_camp_planner mounts the blueprints on a host Flask app
under a URL prefix, with identity supplied by the host's auth callback.
"""

from __future__ import annotations

import pytest
from flask import Flask
from flask_wtf import CSRFProtect

from camp_planner import register_camp_planner
from camp_planner.extensions import db


@pytest.fixture
def embedded():
    """A bare host app with Camp Planner mounted at /planner. Yields (client, identity
    holder) — tests set holder["value"] to the dict the host callback would return."""
    holder: dict = {"value": None}
    host = Flask(__name__)
    host.config.update(SECRET_KEY="test", TESTING=True, WTF_CSRF_ENABLED=False)
    CSRFProtect(host)   # the host owns CSRF; our templates call its csrf_token()
    register_camp_planner(host, auth_callback=lambda: holder["value"],
                          url_prefix="/planner",
                          database_uri="sqlite:///:memory:")
    with host.app_context():
        db.create_all()
        yield host.test_client(), holder
        db.session.remove()


def _admin(holder):
    holder["value"] = {"user_id": "host-admin", "display_name": "Host Admin", "is_admin": True}


def test_mounts_under_prefix_with_host_identity(embedded):
    client, holder = embedded

    # anonymous (callback returns None): the landing page renders, links carry the prefix
    html = client.get("/planner/").get_data(as_text=True)
    assert "nejste přihlášeni" in html
    assert 'href="/planner/"' in html                    # url_for is prefix-safe

    # the host's admin identity drives the API mounted under the prefix
    _admin(holder)
    resp = client.post("/planner/api/camps", json={
        "name": "Tábor", "slug": "t", "start_date": "2026-07-04", "length_days": 3,
        "timezone": "Europe/Prague", "window_start_min": 240, "snap_minutes": 15})
    assert resp.status_code == 200

    html = client.get("/planner/camps/t").get_data(as_text=True)
    assert 'id="cp-timeline-data"' in html
    assert "/planner/api/camps/t/timeline" in html       # embedded api urls carry the prefix
    assert "/planner/camps/t/activities/0" in html        # page urls too


def test_slug_grants_scope_access(embedded):
    client, holder = embedded
    _admin(holder)
    client.post("/planner/api/camps", json={
        "name": "Tábor", "slug": "t", "start_date": "2026-07-04", "length_days": 3,
        "timezone": "Europe/Prague", "window_start_min": 240, "snap_minutes": 15})

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
