"""Injected-session mode: register_camp_planner(session=...) makes the planner run on
the host's session instead of an engine and pool of its own (docs/DEPLOYMENT.md §2).

The API tests are a slice of test_api / test_api_tokens re-run in that mode: what they
assert is that our commit/flush/rollback flows work on a session we did not create.
"""

from __future__ import annotations

import pytest
from flask import Flask
from flask.globals import app_ctx
from flask_wtf import CSRFProtect
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session as SASession
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

from camp_planner import register_camp_planner
from camp_planner.extensions import Base
from camp_planner.models.camp import Camp
from tests.conftest import HOST_ADMIN, make_camp_embedded


@pytest.fixture
def host():
    """A host owning engine + session, planner mounted at /planner; yields
    (client, identity holder) like test_embedded's `embedded`."""
    # One shared in-memory connection: a new one would see an empty database.
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Session = scoped_session(sessionmaker(bind=engine),
                             scopefunc=lambda: app_ctx._get_current_object())

    holder: dict = {"value": None}
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test", TESTING=True, WTF_CSRF_ENABLED=False)
    CSRFProtect(app)
    app.teardown_appcontext(lambda exc: Session.remove())

    register_camp_planner(app, auth_callback=lambda: holder["value"],
                          url_prefix="/planner", session=Session)

    with app.app_context():
        Base.metadata.create_all(engine)
        Session.remove()
    # No app context held open on purpose: Flask would reuse it instead of pushing a
    # per-request one, and the host's teardown would never run between requests.
    yield app.test_client(), holder
    engine.dispose()


@pytest.fixture
def admin(host):
    """The host fixture with the callback already supplying an admin identity."""
    client, holder = host
    holder["value"] = HOST_ADMIN
    return client


def _ok(resp):
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


# --- the planner's own flows, on the host's session --------------------------

def test_we_add_no_engine_and_no_teardown_to_the_host(host):
    """The whole point: we call no init_app, so nothing of ours is bound to a database
    (no second pool) and nothing of ours runs at teardown. The session is the host's to
    scope and dispose of."""
    from camp_planner.extensions import db

    client, _ = host
    app = client.application
    assert "sqlalchemy" not in app.extensions
    with pytest.raises(RuntimeError):
        db.engine
    assert len(app.teardown_appcontext_funcs) == 1      # the fixture's own Session.remove


def test_activity_crud(admin):
    camp = make_camp_embedded(admin)
    slug = camp["slug"]
    cat_id = _ok(admin.put(f"/planner/api/camps/{slug}/categories",
                           json={"items": [{"key": "hra", "label": "Hra",
                                            "color": "#0b8043"}]}))["items"][0]["id"]

    created = _ok(admin.post(f"/planner/api/camps/{slug}/activities",
                             json={"title": "Akce", "category_id": cat_id}))["activity"]
    aid = created["id"]

    updated = _ok(admin.patch(f"/planner/api/activities/{aid}",
                              json={"title": "Akce II"}))["activity"]
    assert updated["title"] == "Akce II"

    # the commit really landed: a fresh request (fresh session) reads it back
    assert _ok(admin.get(f"/planner/api/activities/{aid}"))["activity"]["title"] == "Akce II"

    assert _ok(admin.delete(f"/planner/api/activities/{aid}"))["ok"]
    assert admin.get(f"/planner/api/activities/{aid}").status_code == 404


def test_material_duplicate_rolls_back_and_leaves_the_session_usable(admin):
    """The IntegrityError branch (create_material): the service rolls back after a failed
    flush, and the request that follows still works, on the host's session too."""
    slug = make_camp_embedded(admin)["slug"]
    _ok(admin.post(f"/planner/api/camps/{slug}/materials", json={"name": "A4 papír"}))

    dup = admin.post(f"/planner/api/camps/{slug}/materials", json={"name": "papír A4"})
    assert dup.status_code == 400 and "existuje" in dup.get_json()["error"]

    _ok(admin.post(f"/planner/api/camps/{slug}/materials", json={"name": "Lano"}))
    names = [m["name"] for m in _ok(admin.get(f"/planner/api/camps/{slug}/materials"))["materials"]]
    assert names == ["A4 papír", "Lano"]


def test_api_tokens(admin):
    slug = make_camp_embedded(admin)["slug"]
    body = _ok(admin.post(f"/planner/api/camps/{slug}/tokens",
                          json={"name": "sync", "role": "editor"}))
    assert body["secret"].startswith("cp_")
    token_id = body["token"]["id"]

    # the bearer token authenticates a write of its own (a path with no host identity)
    bearer = {"Authorization": f"Bearer {body['secret']}"}
    assert admin.patch(f"/planner/api/camps/{slug}", json={"length_days": 4},
                       headers=bearer).status_code == 200

    _ok(admin.delete(f"/planner/api/tokens/{token_id}"))
    assert admin.patch(f"/planner/api/camps/{slug}", json={"length_days": 5},
                       headers=bearer).status_code == 401


def test_pages_render(admin):
    slug = make_camp_embedded(admin)["slug"]
    html = admin.get(f"/planner/camps/{slug}").get_data(as_text=True)
    assert 'id="cp-timeline-data"' in html


# --- the contract ------------------------------------------------------------

def test_an_open_transaction_at_request_entry_is_refused(host):
    """Autobegin means a bare SELECT leaves a transaction open; entering the planner with
    one would let our mid-request commit end a unit of work the host still owns."""
    client, holder = host
    holder["value"] = HOST_ADMIN
    app = client.application
    host_session = app.extensions["camp_planner"]["session"]

    @app.before_request
    def _host_work():                 # app-level hooks run before any blueprint's
        host_session().execute(select(1))  # the host's own query, left uncommitted

    with pytest.raises(RuntimeError, match="active transaction at request entry"):
        client.get("/planner/")


def test_the_check_runs_before_the_apis_own_token_hook(admin):
    """Ordering (see integration._wire_blueprint): a Bearer request makes api_token_auth
    query the DB, which autobegins. Were the contract check registered after it, that
    transaction would be ours and every token request would raise."""
    slug = make_camp_embedded(admin)["slug"]
    secret = _ok(admin.post(f"/planner/api/camps/{slug}/tokens",
                            json={"name": "sync", "role": "viewer"}))["secret"]

    resp = admin.get(f"/planner/api/camps/{slug}",
                     headers={"Authorization": f"Bearer {secret}"})
    assert resp.status_code == 200


def test_a_plain_session_works_too():
    """`session=` takes the session itself, so a host with a single one needn't wrap it;
    a scoped_session is merely the usual way to have one per request."""
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    plain = SASession(engine)

    app = Flask(__name__)
    app.config.update(SECRET_KEY="test", TESTING=True, WTF_CSRF_ENABLED=False)
    CSRFProtect(app)
    register_camp_planner(app, auth_callback=lambda: {"user_id": "a", "is_admin": True},
                          url_prefix="/planner", session=plain)
    client = app.test_client()

    assert make_camp_embedded(client)["slug"] == "t"
    assert plain.scalar(select(Camp.slug)) == "t"      # the very session we handed over
    plain.rollback()                                   # the read above autobegan one
    assert _ok(client.get("/planner/api/camps"))["camps"][0]["slug"] == "t"
    plain.close()
    engine.dispose()


def test_session_and_database_uri_are_mutually_exclusive():
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test")
    with pytest.raises(ValueError, match="mutually exclusive"):
        register_camp_planner(app, auth_callback=lambda: None,
                              database_uri="sqlite:///:memory:",
                              session=scoped_session(sessionmaker()))
