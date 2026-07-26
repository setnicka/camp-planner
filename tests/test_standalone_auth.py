"""Standalone auth (the default AUTH_MODE): login/logout, the open-redirect guard,
stale sessions, self-lockout guards and the grants admin.

The shared conftest runs proxy mode; this module builds its own app with
AUTH_MODE=standalone.
"""

from __future__ import annotations

from datetime import date

import pytest

from camp_planner import create_app
from camp_planner.config import TestingConfig
from camp_planner.extensions import db
from camp_planner.models.auth import User, UserCampRole
from camp_planner.models.camp import Camp


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(TestingConfig, "AUTH_MODE", "standalone")
    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


def _add_user(username="franta", password="tajne", *, admin=False) -> User:
    user = User(username=username, display_name=username.capitalize(), is_admin=admin)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def _login(client, username="franta", password="tajne", next_=""):
    return client.post("/auth/login",
                       data={"username": username, "password": password, "next": next_})


# --- login / logout ------------------------------------------------------------------

def test_login_success_and_logout(client, app):
    _add_user()
    resp = _login(client)
    assert resp.status_code == 302 and resp.headers["Location"] == "/"

    html = client.get("/").get_data(as_text=True)
    assert "Franta" in html and "Odhlásit se" in html    # logged in, display name shown

    assert client.post("/auth/logout").status_code == 302
    html = client.get("/").get_data(as_text=True)
    assert "Franta" not in html and "Přihlásit se" in html


def test_login_wrong_password_refills_username(client):
    _add_user()
    resp = _login(client, password="spatne")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200                        # re-rendered, not redirected
    assert "Neplatné uživatelské jméno nebo heslo." in html
    assert 'value="franta"' in html                       # typed username survives

    # unknown user gets the identical answer (no username enumeration)
    resp = _login(client, username="neexistuje")
    assert "Neplatné uživatelské jméno nebo heslo." in resp.get_data(as_text=True)


def test_stale_session_is_anonymous_not_500(client):
    user = _add_user()
    _login(client)
    db.session.delete(user)                               # account removed mid-session
    db.session.commit()

    resp = client.get("/")
    assert resp.status_code == 200                        # request survives
    assert "Franta" not in resp.get_data(as_text=True)    # treated as anonymous


def test_anonymous_admin_page_redirects_to_login(client):
    resp = client.get("/auth/users")
    assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]


def test_non_admin_cannot_manage_users(client):
    _add_user()
    _login(client)
    assert client.get("/auth/users").status_code == 403


# --- the `next` redirect target (open-redirect guard) --------------------------------

@pytest.mark.parametrize(("target", "allowed"), [
    ("/camps/tabor", True),               # same-site path → honored
    ("https://evil.example", False),      # absolute URL → refused
    ("//evil.example", False),            # protocol-relative → refused
    ("/\\evil.example", False),           # backslash trick (browsers fold \ to /) → refused
    ("relative/path", False),             # not root-relative → refused
])
def test_login_next_redirect_guard(client, target, allowed):
    _add_user()
    resp = _login(client, next_=target)
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert (location == target) if allowed else (location == "/")


# --- user admin: self-lockout guards --------------------------------------------------

def test_admin_cannot_delete_own_account(client):
    admin = _add_user("boss", admin=True)
    _login(client, "boss")
    resp = client.post(f"/auth/users/{admin.id}/delete", follow_redirects=True)
    assert "Nemůžete smazat vlastní účet." in resp.get_data(as_text=True)
    assert db.session.get(User, admin.id) is not None


def test_admin_cannot_strip_own_admin_flag(client):
    admin = _add_user("boss", admin=True)
    _login(client, "boss")
    # the form omits is_admin (a disabled checkbox posts nothing) — admin must survive
    client.post(f"/auth/users/{admin.id}/profile",
                data={"username": "boss", "display_name": "Boss"})
    assert db.session.get(User, admin.id).is_admin is True


# --- grants admin ---------------------------------------------------------------------

def test_grant_add_duplicate_and_remove(client, app):
    admin = _add_user("boss", admin=True)
    user = _add_user()
    camp = Camp(name="Tábor", slug="t", start_date=date(2026, 7, 4),
                length_days=3, window_start_min=240, snap_minutes=15)
    db.session.add(camp)
    db.session.commit()
    _login(client, "boss")

    resp = client.post(f"/auth/users/{user.id}/grants",
                       data={"role": "editor", "camp_id": str(camp.id)}, follow_redirects=True)
    assert "Oprávnění bylo přidáno." in resp.get_data(as_text=True)
    grant = db.session.scalar(db.select(UserCampRole).filter_by(user_id=user.id))
    assert grant.camp_id == camp.id and grant.role.value == "editor"

    # the same grant again is refused
    resp = client.post(f"/auth/users/{user.id}/grants",
                       data={"role": "editor", "camp_id": str(camp.id)}, follow_redirects=True)
    assert "Toto oprávnění už existuje." in resp.get_data(as_text=True)

    # removing it through another user's URL is a 404 (no cross-user removal)
    assert client.post(f"/auth/users/{admin.id}/grants/{grant.id}/delete").status_code == 404
    resp = client.post(f"/auth/users/{user.id}/grants/{grant.id}/delete", follow_redirects=True)
    assert "Oprávnění bylo odebráno." in resp.get_data(as_text=True)
    assert db.session.scalar(db.select(UserCampRole).filter_by(user_id=user.id)) is None
