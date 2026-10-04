"""API bearer tokens: CLI, the token-management endpoints, and Bearer authentication
(scoping, revocation, the CSRF exemption, and the token-can't-manage-tokens rule)."""

from __future__ import annotations

import pytest

from camp_planner import create_app
from camp_planner.extensions import db
from camp_planner.auth.identity import CampRole
from camp_planner.models.auth import ApiToken
from camp_planner.models.camp import Camp
from camp_planner.services import api_tokens
from tests.conftest import ADMIN, audit, editor, get_json, make_camp, ok, viewer


def _create(client, slug, name="import", role="viewer", headers=ADMIN):
    return client.post(f"/api/camps/{slug}/tokens", json={"name": name, "role": role}, headers=headers)


def _bearer(secret):
    return {"Authorization": f"Bearer {secret}"}


# --- management endpoints -------------------------------------------------------------

def test_create_returns_secret_once_and_lists_without_it(client, seeded):
    slug = seeded["slug"]
    resp = _create(client, slug, name="sync", role="editor")
    assert resp.status_code == 200
    body = resp.get_json()
    secret = body["secret"]
    assert secret.startswith("cp_")
    assert body["token"]["name"] == "sync" and body["token"]["role"] == "editor"
    assert body["token"]["created_by"] == "admin"        # the session user
    assert "secret" not in body["token"]

    listed = client.get(f"/api/camps/{slug}/tokens", headers=ADMIN).get_json()["tokens"]
    assert [t["name"] for t in listed] == ["sync"]
    assert all("secret" not in t and "token_hash" not in t for t in listed)


def test_editor_may_manage_viewer_cannot(client, seeded):
    slug = seeded["slug"]
    assert _create(client, slug, headers=editor(slug)).status_code == 200   # editors too
    assert _create(client, slug, name="x", headers=viewer(slug)).status_code == 403
    assert client.get(f"/api/camps/{slug}/tokens", headers=viewer(slug)).status_code == 403


def test_duplicate_name_is_400(client, seeded):
    slug = seeded["slug"]
    assert _create(client, slug, name="dup").status_code == 200
    resp = _create(client, slug, name="dup")
    assert resp.status_code == 400 and "existuje" in resp.get_json()["error"]


def test_same_name_allowed_in_different_camps(client, seeded):
    slug = seeded["slug"]
    other = make_camp(client, "u")["slug"]
    assert _create(client, slug, name="import").status_code == 200
    assert _create(client, other, name="import").status_code == 200   # names are per-camp


def test_revoke_removes_the_token_and_both_ends_are_audited(client, seeded):
    slug = seeded["slug"]
    tid = ok(_create(client, slug))["token"]["id"]
    assert client.delete(f"/api/tokens/{tid}", headers=ADMIN).status_code == 200
    assert get_json(client, f"/api/camps/{slug}/tokens")["tokens"] == []
    assert client.delete(f"/api/tokens/{tid}", headers=ADMIN).status_code == 404   # already gone

    rows = audit(client, slug, entity_type="api_token")
    assert sorted(r["action"] for r in rows) == ["create", "delete"]
    assert all(r["entity_id"] == tid and r["author"] == "admin" for r in rows)


# --- Bearer authentication ------------------------------------------------------------

@pytest.mark.parametrize("role, method, url, body, status", [
    # no session and no CSRF token, scoped to its camp by its role
    ("editor", "get", "/api/camps/t", None, 200),
    ("editor", "patch", "/api/camps/t", {"length_days": 5}, 200),
    ("viewer", "get", "/api/camps/t", None, 200),
    ("viewer", "patch", "/api/camps/t", {"length_days": 5}, 403),
    ("editor", "get", "/api/camps/jina", None, 403),
    # even an editor token may not manage tokens
    ("editor", "get", "/api/camps/t/tokens", None, 403),
    ("editor", "post", "/api/camps/t/tokens", {"name": "x", "role": "viewer"}, 403),
])
def test_token_scope(client, seeded, role, method, url, body, status):
    make_camp(client, "jina")
    secret = ok(_create(client, seeded["slug"], role=role))["secret"]
    resp = client.open(url, method=method, json=body, headers=_bearer(secret))
    assert resp.status_code == status
    if status == 200:   # its own camp, and an editor's write lands
        camp = resp.get_json()["camp"]
        assert (camp["slug"], camp["length_days"]) == ("t", (body or {}).get("length_days", 3))


def test_revoked_and_malformed_tokens_fail_closed(client, seeded):
    slug = seeded["slug"]
    created = ok(_create(client, slug, role="editor"))
    secret = created["secret"]
    assert client.get(f"/api/camps/{slug}", headers=_bearer("cp_nope")).status_code == 401

    client.delete(f"/api/tokens/{created['token']['id']}", headers=ADMIN)
    assert client.get(f"/api/camps/{slug}", headers=_bearer(secret)).status_code == 401


def test_last_used_at_is_set_and_throttled(app, client, seeded):
    slug = seeded["slug"]
    secret = _create(client, slug).get_json()["secret"]
    assert client.get(f"/api/camps/{slug}", headers=_bearer(secret)).status_code == 200

    token = db.session.scalar(db.select(ApiToken))
    first = token.last_used_at
    assert first is not None

    client.get(f"/api/camps/{slug}", headers=_bearer(secret))   # within the throttle window
    db.session.refresh(token)
    assert token.last_used_at == first   # not rewritten on every call


# --- CSRF: cookie requests only -------------------------------------------------------

def test_csrf_guards_cookie_requests_only():
    """The suite runs with CSRF off, so the bearer exemption is only testable here."""
    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    with app.app_context():
        db.create_all()
    c = app.test_client()
    body = {"name": "T", "slug": "t", "start_date": "2026-07-01", "length_days": 3,
            "timezone": "Europe/Prague", "window_start_min": 240, "snap_minutes": 15}

    missing = c.post("/api/camps", json=body, headers=ADMIN)   # the 400 shape the client detects
    assert missing.status_code == 400 and "csrf" in missing.get_json()["error"].lower()
    token = c.get("/csrf-token", headers=ADMIN).get_json()["csrf_token"]
    assert c.post("/api/camps", json=body, headers={**ADMIN, "X-CSRFToken": token}).status_code == 200

    with app.app_context():
        camp = db.session.scalar(db.select(Camp))
        _token, secret = api_tokens.create(camp, "sync", CampRole.editor, "tester")
    patch = {"length_days": 4}
    assert c.patch("/api/camps/t", json=patch, headers=_bearer(secret)).status_code == 200
    # a presented but unknown token is a failed auth, not a CSRF 400
    assert c.patch("/api/camps/t", json=patch, headers=_bearer("cp_nope")).status_code == 401


# --- CLI ------------------------------------------------------------------------------

def test_cli_create_list_revoke(app, seeded):
    slug = seeded["slug"]
    runner = app.test_cli_runner()

    res = runner.invoke(args=["api-token", "create", "sync", "--camp", slug,
                              "--role", "editor", "--created-by", "cron"])
    assert res.exit_code == 0, res.output
    secret = res.output.split("shown once):")[1].strip()
    assert secret.startswith("cp_")
    token = api_tokens.authenticate(secret)
    assert token is not None and token.role.value == "editor" and token.created_by == "cron"

    res = runner.invoke(args=["api-token", "list", "--camp", slug])
    assert "sync" in res.output and slug in res.output and secret not in res.output

    dup = runner.invoke(args=["api-token", "create", "sync", "--camp", slug])
    assert "existuje" in dup.output

    res = runner.invoke(args=["api-token", "revoke", "sync"])
    assert res.exit_code == 0 and "Revoked" in res.output
    assert api_tokens.authenticate(secret) is None


def test_cli_create_rejects_unknown_camp(app):
    res = app.test_cli_runner().invoke(args=["api-token", "create", "x", "--camp", "neexistuje"])
    assert res.exit_code != 0 and "no camp with slug" in res.output
