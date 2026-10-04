"""Shared fixtures for the API tests.

Runs the app in proxy-auth mode so a test can "log in" just by setting the
X-Remote-User / X-Remote-Roles headers (admin, or editor/viewer scoped to a camp
slug). CSRF is disabled so JSON mutations don't need a token. The DB is a fresh
in-memory SQLite per test (TestingConfig).
"""

from __future__ import annotations

import io
import json
import os
import re

# Must be set before camp_planner.config is imported (read at import time). Hard
# assignment so a developer's exported AUTH_MODE can't flip the suite.
os.environ["AUTH_MODE"] = "proxy"
os.environ["SECRET_KEY"] = "test-secret"

from datetime import date  # noqa: E402
from urllib.parse import urlencode  # noqa: E402

import pytest  # noqa: E402
from flask import g  # noqa: E402
from sqlalchemy import event  # noqa: E402

from camp_planner import create_app  # noqa: E402
from camp_planner.extensions import db  # noqa: E402
from camp_planner.models.activity import Activity  # noqa: E402
from camp_planner.models.camp import Camp, Category, Tag, TagKind  # noqa: E402
from camp_planner.models.org import Org  # noqa: E402


@pytest.fixture
def app():
    app = create_app("testing")   # in-memory SQLite; evaporates with the app's engine
    app.config["WTF_CSRF_ENABLED"] = False
    # Requests share this fixture's app context, hence its g: a token request's g.api_token
    # would make the next request skip the provider. g.identity stays for direct service calls.
    app.teardown_request(lambda exc: g.pop("api_token", None))
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seeded(app):
    """A camp with one category, org, tag and activity. Returns the ids/slug."""
    camp = Camp(name="Tábor", slug="t", start_date=date(2026, 7, 4), length_days=3,
                window_start_min=240, snap_minutes=15)
    db.session.add(camp)
    db.session.flush()
    cat = Category(camp_id=camp.id, key="hra", label="Hra", color="#0b8043", sort_order=0)
    org = Org(camp_id=camp.id, name="Karel", initials="K")
    tag = Tag(camp_id=camp.id, name="Důležité", kind=TagKind.text)
    db.session.add_all([cat, org, tag])
    db.session.flush()
    activity = Activity(camp_id=camp.id, title="Akce", category_id=cat.id)
    db.session.add(activity)
    db.session.commit()
    return {
        "slug": camp.slug, "camp_id": camp.id, "cat_id": cat.id,
        "org_id": org.id, "tag_id": tag.id, "activity_id": activity.id,
    }


# --- auth headers ------------------------------------------------------------

ADMIN = {"X-Remote-User": "admin", "X-Remote-Roles": "admin"}

# What an embedded host's auth callback returns for its admin; there, headers carry nothing.
HOST_ADMIN = {"user_id": "host-admin", "display_name": "Host Admin", "is_admin": True}


def editor(slug: str) -> dict:
    return {"X-Remote-User": "ed", "X-Remote-Roles": f"editor:{slug}"}


def viewer(slug: str) -> dict:
    return {"X-Remote-User": "vi", "X-Remote-Roles": f"viewer:{slug}"}


# --- requests: every make_* asserts its request succeeded ---------------------

def ok(resp) -> dict:
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def get_json(client, url, headers=ADMIN) -> dict:
    return ok(client.get(url, headers=headers))


def make_camp(client, slug, prefix="", **overrides) -> dict:
    """Create a camp via the API (as admin) and return its envelope dict. Embedded tests
    pass the mount prefix; the host's callback then supplies the identity."""
    body = {"name": slug.capitalize(), "slug": slug, "start_date": "2026-09-01",
            "length_days": 3, "timezone": "Europe/Prague",
            "window_start_min": 240, "snap_minutes": 15, **overrides}
    return ok(client.post(f"{prefix}/api/camps", json=body, headers=ADMIN))["camp"]


def make_activity(client, slug, title="Druhá") -> int:
    return ok(client.post(f"/api/camps/{slug}/activities", json={"title": title},
                          headers=ADMIN))["activity"]["id"]


def make_material(client, slug, name="A4 papír", **fields) -> dict:
    return ok(client.post(f"/api/camps/{slug}/materials", json={"name": name, **fields},
                          headers=ADMIN))["material"]


def make_slot(client, slug, activity_id, start="2026-07-04T14:00", end="2026-07-04T16:00",
              role="main") -> int:
    """Create a slot via the timeline batch (the only placement path) and return its id."""
    rev = get_json(client, f"/api/camps/{slug}/timeline")["camp"]["rev"]
    created = {"activity_id": activity_id, "role": role, "start_at": start, "end_at": end}
    resp = client.patch(f"/api/camps/{slug}/timeline", json={"rev": rev, "creates": [created]},
                        headers=ADMIN)
    return ok(resp)["created"][0]["id"]


def add_org(camp_id, initials, name) -> Org:
    org = Org(camp_id=camp_id, name=name, initials=initials)
    db.session.add(org)
    db.session.flush()
    return org


def audit(client, slug, **query) -> list[dict]:
    """The camp's audit feed, newest first."""
    return get_json(client, f"/api/camps/{slug}/audit?" + urlencode(query))["entries"]


# --- the global warehouse ----------------------------------------------------
# Shared by the warehouse tests and the camp-material link tests, so neither imports the
# other's module (which pytest would then hold twice, once per import name).

def make_box(client, name="Krabice 1", **fields) -> dict:
    return ok(client.post("/api/inventory/boxes", json={"name": name, **fields},
                          headers=ADMIN))["box"]


def make_item(client, box_id, name="Lano", **fields) -> dict:
    return ok(client.post("/api/inventory/items",
                          json={"name": name, "box_id": box_id, **fields}, headers=ADMIN))["item"]


def start_check(client, name="Inventura 2026") -> dict:
    return ok(client.post("/api/inventory/checks", json={"name": name}, headers=ADMIN))["check"]


def observe(client, in_box, item_id, headers=ADMIN, **body):
    """Record an observation of `item_id` while standing in box `in_box`. A box_id in the
    body is where the thing actually is, so it can differ (that is a move)."""
    return client.put(f"/api/inventory/boxes/{in_box}/records/{item_id}",
                      json=body, headers=headers)


def complete(client, check, headers=ADMIN):
    return client.post(f"/api/inventory/checks/{check['id']}/complete", headers=headers)


def discard(client, item, headers=ADMIN):
    return client.post(f"/api/inventory/items/{item['id']}/discard", headers=headers)


@pytest.fixture
def box(client):
    return make_box(client)


@pytest.fixture
def media_dir(app, tmp_path):
    """Photos switched on, storing into a scratch directory."""
    app.extensions["camp_planner"]["media_dir"] = str(tmp_path)
    return tmp_path


def box_state(client, box_id, headers=ADMIN) -> dict:
    return client.get(f"/api/inventory/boxes/{box_id}/state", headers=headers).get_json()["state"]


def page_data(client, url, headers=ADMIN, script="cp-inventory-data") -> dict:
    """The JSON a page inlines for its script (no page fetches on load)."""
    html = client.get(url, headers=headers).get_data(as_text=True)
    match = re.search(rf'<script id="{script}" type="application/json">(.*?)</script>',
                      html, re.S)
    assert match, f"{url} inlined no data"
    return json.loads(match.group(1))


def get_item(client, item_id) -> dict:
    """Read an item back the way the pages do: there is no single-item GET."""
    data = page_data(client, "/inventory")
    items = [i for entry in data["boxes"] for i in entry["items"]] + data["discarded"]
    return next(i for i in items if i["id"] == item_id)


# --- photos ------------------------------------------------------------------

def png(size=(64, 48)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, "green").save(buffer, "PNG")
    return buffer.getvalue()


def upload(client, item_id, data=None):
    return client.post(f"/api/inventory/items/{item_id}/photos", headers=ADMIN,
                       data={"photos": (io.BytesIO(data or png()), "foto.png")},
                       content_type="multipart/form-data")


def count_queries(fn) -> int:
    statements: list[str] = []

    def listener(conn, cursor, statement, *_, **__):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", listener)
    try:
        fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", listener)
    return len(statements)
