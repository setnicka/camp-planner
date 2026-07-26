"""API bearer tokens: CLI, the token-management endpoints, and Bearer authentication
(scoping, revocation, the CSRF exemption, and the token-can't-manage-tokens rule)."""

from __future__ import annotations

from camp_planner import create_app
from camp_planner.extensions import db
from camp_planner.models.auth import ApiToken
from camp_planner.services import api_tokens
from tests.conftest import ADMIN, editor, make_camp, viewer


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


def test_create_and_revoke_are_audited(client, seeded):
    slug = seeded["slug"]
    tid = _create(client, slug, name="audited").get_json()["token"]["id"]
    assert client.delete(f"/api/tokens/{tid}", headers=ADMIN).status_code == 200
    rows = client.get(f"/api/camps/{slug}/audit?entity_type=api_token",
                      headers=ADMIN).get_json()["entries"]
    assert sorted(r["action"] for r in rows) == ["create", "delete"]
    assert all(r["entity_id"] == tid and r["author"] == "admin" for r in rows)


def test_revoke_removes_the_token(client, seeded):
    slug = seeded["slug"]
    tid = _create(client, slug).get_json()["token"]["id"]
    assert client.delete(f"/api/tokens/{tid}", headers=ADMIN).status_code == 200
    assert client.get(f"/api/camps/{slug}/tokens", headers=ADMIN).get_json()["tokens"] == []
    assert client.delete(f"/api/tokens/{tid}", headers=ADMIN).status_code == 404   # already gone


# --- Bearer authentication ------------------------------------------------------------

def test_invalid_bearer_is_401_not_csrf_400(client, seeded):
    slug = seeded["slug"]
    # a presented-but-unknown token on a write is a failed auth (401), not a CSRF 400
    resp = client.patch(f"/api/camps/{slug}", json={"length_days": 5}, headers=_bearer("cp_nope"))
    assert resp.status_code == 401


def test_token_authenticates_scoped_to_its_camp(client, seeded):
    slug = seeded["slug"]
    secret = _create(client, slug, role="editor").get_json()["secret"]

    # a viewer/editor token reaches its camp's API with no session, no CSRF token
    got = client.get(f"/api/camps/{slug}", headers=_bearer(secret))
    assert got.status_code == 200 and got.get_json()["camp"]["slug"] == slug

    # and it can mutate (editor) without an X-CSRFToken header — CSRF is cookie-only
    patched = client.patch(f"/api/camps/{slug}", json={"length_days": 5}, headers=_bearer(secret))
    assert patched.status_code == 200 and patched.get_json()["camp"]["length_days"] == 5


def test_viewer_token_cannot_edit(client, seeded):
    slug = seeded["slug"]
    secret = _create(client, slug, role="viewer").get_json()["secret"]
    assert client.get(f"/api/camps/{slug}", headers=_bearer(secret)).status_code == 200
    assert client.patch(f"/api/camps/{slug}", json={"length_days": 5},
                        headers=_bearer(secret)).status_code == 403


def test_token_cannot_reach_another_camp(client, seeded):
    slug = seeded["slug"]
    make_camp(client, "jina")
    secret = _create(client, slug, role="editor").get_json()["secret"]
    assert client.get("/api/camps/jina", headers=_bearer(secret)).status_code == 403


def test_revoked_and_malformed_tokens_fail_closed(client, seeded):
    slug = seeded["slug"]
    created = _create(client, slug, role="editor").get_json()
    secret = created["secret"]
    # a bogus secret → no identity → 401 (not 200/500)
    assert client.get(f"/api/camps/{slug}", headers=_bearer("cp_nope")).status_code == 401

    client.delete(f"/api/tokens/{created['token']['id']}", headers=ADMIN)
    assert client.get(f"/api/camps/{slug}", headers=_bearer(secret)).status_code == 401


def test_token_cannot_manage_tokens(client, seeded):
    slug = seeded["slug"]
    secret = _create(client, slug, role="editor").get_json()["secret"]
    # even an editor token is refused on the token-management endpoints
    assert client.get(f"/api/camps/{slug}/tokens", headers=_bearer(secret)).status_code == 403
    assert client.post(f"/api/camps/{slug}/tokens", json={"name": "x", "role": "viewer"},
                       headers=_bearer(secret)).status_code == 403


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


# --- CSRF interaction for cookie (non-token) requests ---------------------------------

def test_cookie_mutation_still_requires_csrf():
    """The exemption is for token requests only; a cookie-authed API mutation without an
    X-CSRFToken header is still rejected."""
    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    with app.app_context():
        db.create_all()
    c = app.test_client()
    body = {"name": "T", "slug": "t", "start_date": "2026-07-01", "length_days": 3,
            "timezone": "Europe/Prague", "window_start_min": 240, "snap_minutes": 15}
    resp = c.post("/api/camps", json=body, headers=ADMIN)
    assert resp.status_code == 400 and "csrf" in resp.get_json()["error"].lower()


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
