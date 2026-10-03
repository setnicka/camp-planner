"""Bearer-token identity for the JSON API.

Resolved on the api blueprint before the configured session/proxy/callback provider:
a request carrying `Authorization: Bearer cp_…` is authenticated as the token's
camp-scoped identity (user_id "token:<id>"), independent of AUTH_MODE. Without a Bearer
header the normal provider takes over (browser calls are unaffected).
"""

from __future__ import annotations

from flask import abort, current_app, g, request

from camp_planner.auth.identity import build_identity
from camp_planner.extensions import csrf
from camp_planner.services import api_tokens


def authenticate() -> None:
    """The api blueprint's auth hook: a Bearer token sets g.identity and g.api_token, a bad
    one is 401 (not csrf.protect()'s misleading 400); a cookie request gets the CSRF check
    the blueprint is exempted from (a no-op on safe methods). Registered by
    integration._wire_blueprint, which owns the hook order."""
    scheme, _, secret = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        if current_app.config.get("WTF_CSRF_ENABLED", True):
            csrf.protect()
        return
    token = api_tokens.authenticate(secret.strip()) if secret.strip() else None
    if token is None:
        abort(401, "Neplatný nebo odvolaný API token.")
    api_tokens.touch(token)
    g.api_token = token
    g.identity = build_identity(
        # id, not name: names are unique only per camp
        user_id=f"token:{token.id}",
        raw_grants=[(token.role, frozenset({token.camp_id}))],
    )
