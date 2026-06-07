"""Proxy auth: identity from trusted X-Remote-* headers set by a reverse proxy.

SECURITY: these headers are trustworthy ONLY when a reverse proxy is the sole
route to this app and overwrites any client-supplied X-Remote-* on every
request. Bind the app to localhost/an internal interface and never enable proxy
mode on a directly reachable process — otherwise a client can forge identities.

Header grammar:
  X-Remote-User   stable id (feeds AuditLog.author) — its presence = authenticated
  X-Remote-Name   display name (optional; falls back to the user id). May be
                  percent-encoded UTF-8, since HTTP headers carry only latin-1.
  X-Remote-Roles  space-separated tokens:
                    admin                       -> global admin flag
                    editor:* / viewer:*         -> role over all camps
                    editor:smf-2026,letni-tabor -> role scoped to those camp slugs

Scopes are camp *slugs* (what the upstream app knows), resolved to ids per request
against the current camps; slugs of unknown/not-yet-created camps are ignored.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import unquote

from flask import request

from camp_planner.auth.identity import ANONYMOUS, CampRole, build_identity
from camp_planner.auth.scopes import resolve_slug_grants

if TYPE_CHECKING:
    from camp_planner.auth.identity import Identity, Scope


def _grants_from_tokens(tokens: list[str]) -> list[tuple[CampRole, Scope]]:
    """Parse role tokens into id-based grants. Scopes are camp slugs (resolved to
    ids); unknown roles and unknown/not-yet-created slugs are ignored."""
    parsed: list[tuple[CampRole, set[str] | None]] = []  # None scope = all camps
    for token in tokens:
        if token == "admin":
            continue  # represented by the is_admin flag, not a grant
        name, _, scope_str = token.partition(":")
        try:
            role = CampRole(name)
        except ValueError:
            continue  # ignore unknown roles defensively
        slugs = None if scope_str in ("", "*") else {s for s in scope_str.split(",") if s.strip()}
        parsed.append((role, slugs))
    return resolve_slug_grants(parsed)


class ProxyProvider:
    def __init__(self, dev_user: dict | None = None) -> None:
        # dev_user: optional local-dev stand-in for the proxy, e.g.
        #   {"user_id": "dev", "display_name": "Dev", "roles": "admin"}
        self._dev_user = dev_user

    def load_identity(self) -> Identity:
        user = request.headers.get("X-Remote-User")
        if user:
            user_id = user
            # Name may be percent-encoded (HTTP headers are latin-1, names aren't).
            # unquote() is a safe no-op for plain ASCII names without '%'.
            name_header = request.headers.get("X-Remote-Name")
            display_name = unquote(name_header) if name_header else name_header
            roles = request.headers.get("X-Remote-Roles", "")
        elif self._dev_user:  # header-less local dev
            user_id = self._dev_user.get("user_id", "dev")
            display_name = self._dev_user.get("display_name")
            roles = self._dev_user.get("roles", "")
        else:
            return ANONYMOUS
        tokens = roles.split()
        return build_identity(
            user_id=user_id,
            display_name=display_name,  # build_identity falls back to user_id
            is_admin="admin" in tokens,
            raw_grants=_grants_from_tokens(tokens),
        )
