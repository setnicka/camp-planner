"""Bearer-token identity for the JSON API.

Resolved on the api blueprint before the configured session/proxy/callback provider:
a request carrying `Authorization: Bearer cp_…` is authenticated as the token's
camp-scoped identity (user_id "token:<id>"), independent of AUTH_MODE. Absent or
unknown token → None, so the normal provider takes over (browser calls are unaffected).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import g, request

from camp_planner.auth.identity import build_identity
from camp_planner.services import api_tokens

if TYPE_CHECKING:
    from camp_planner.auth.identity import Identity


def resolve_identity() -> Identity | None:
    """The Bearer token's identity (and stash the token on g.api_token), or None."""
    scheme, _, secret = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not secret.strip():
        return None
    token = api_tokens.authenticate(secret.strip())
    if token is None:
        return None
    g.api_token = token
    return build_identity(
        # id, not name — names are unique only per camp
        user_id=f"token:{token.id}",
        raw_grants=[(token.role, frozenset({token.camp_id}))],
    )
