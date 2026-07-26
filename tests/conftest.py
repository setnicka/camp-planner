"""Shared fixtures for the API tests.

Runs the app in proxy-auth mode so a test can "log in" just by setting the
X-Remote-User / X-Remote-Roles headers (admin, or editor/viewer scoped to a camp
slug). CSRF is disabled so JSON mutations don't need a token. The DB is a fresh
in-memory SQLite per test (TestingConfig).
"""

from __future__ import annotations

import os

# Must be set before camp_planner.config is imported (read at import time). Hard
# assignment so a developer's exported AUTH_MODE can't flip the suite.
os.environ["AUTH_MODE"] = "proxy"
os.environ["SECRET_KEY"] = "test-secret"

from datetime import date  # noqa: E402

import pytest  # noqa: E402

from camp_planner import create_app  # noqa: E402
from camp_planner.extensions import db  # noqa: E402
from camp_planner.models.activity import Activity  # noqa: E402
from camp_planner.models.camp import Camp, Category, Tag, TagKind  # noqa: E402
from camp_planner.models.org import Org  # noqa: E402


@pytest.fixture
def app():
    app = create_app("testing")   # in-memory SQLite; evaporates with the app's engine
    app.config["WTF_CSRF_ENABLED"] = False
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


def make_camp(client, slug, **overrides) -> dict:
    """Create a camp via the API (as admin) and return its envelope dict."""
    body = {"name": slug.capitalize(), "slug": slug, "start_date": "2026-09-01",
            "length_days": 3, "timezone": "Europe/Prague",
            "window_start_min": 240, "snap_minutes": 15, **overrides}
    resp = client.post("/api/camps", json=body, headers=ADMIN)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["camp"]


# --- auth header helpers -----------------------------------------------------

ADMIN = {"X-Remote-User": "admin", "X-Remote-Roles": "admin"}


def editor(slug: str) -> dict:
    return {"X-Remote-User": "ed", "X-Remote-Roles": f"editor:{slug}"}


def viewer(slug: str) -> dict:
    return {"X-Remote-User": "vi", "X-Remote-Roles": f"viewer:{slug}"}
