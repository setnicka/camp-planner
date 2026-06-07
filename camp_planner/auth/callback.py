"""Embedded auth: identity supplied by the host app through a callback.

When the host embeds Camp Planner, auth_callback is called (with no arguments)
within the request context, so it can read the host's own session/g. It returns
either:

  - a fully built Identity (host imports camp_planner.auth.identity), or
  - a plain dict the host needn't import anything for:

        {
          "user_id": "foo_bar",
          "display_name": "Foo Bar",          # optional
          "is_admin": False,                  # optional
          "grants": [                         # optional
            {"role": "editor", "camps": ["smf-2026", "letni-tabor"]},
            {"role": "viewer", "camps": "all"},
          ],
        }

camps are camp slugs (or "all"), resolved to ids per request; unknown slugs are
ignored. Return None for an unauthenticated request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from camp_planner.auth.identity import ANONYMOUS, CampRole, Identity, build_identity
from camp_planner.auth.scopes import resolve_slug_grants

if TYPE_CHECKING:
    from camp_planner.auth.identity import Scope


def _coerce_grants(raw: list[dict] | None) -> list[tuple[CampRole, Scope]]:
    parsed: list[tuple[CampRole, set[str] | None]] = []  # None scope = all camps
    for item in raw or []:
        role = CampRole(item["role"])
        camps = item.get("camps", "all")
        slugs = None if camps == "all" else {str(c) for c in camps}
        parsed.append((role, slugs))
    return resolve_slug_grants(parsed)


class CallbackProvider:
    def __init__(self, callback: Callable[[], Identity | dict[str, Any] | None]) -> None:
        self._callback = callback

    def load_identity(self) -> Identity:
        result = self._callback()
        if result is None:
            return ANONYMOUS
        if isinstance(result, Identity):
            return result
        return build_identity(
            user_id=result["user_id"],
            display_name=result.get("display_name"),
            is_admin=result.get("is_admin", False),
            raw_grants=_coerce_grants(result.get("grants")),
        )
