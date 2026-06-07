"""The auth contract: a provider-neutral Identity every mode produces.

Every auth mode (standalone / proxy / embedded) resolves the user into the same
Identity; the rest of the app reads only Identity and the permission helpers.

Imports nothing from the package, so both the models and the providers can import
it without a cycle.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Protocol


class CampRole(str, enum.Enum):
    editor = "editor"   # edit everything in a camp except its name/slug
    viewer = "viewer"   # read-only access to a camp and its contents


class _All:
    """Sentinel for an unscoped grant (all camps) — a distinct type, not None, so a
    scope is unambiguously ALL or a set of camp ids."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "ALL"


ALL = _All()

# A grant either covers specific camp ids or every camp (ALL).
Scope = frozenset[int] | _All


@dataclass(frozen=True)
class Grant:
    """One role held over a scope (specific camp ids, or ALL camps)."""

    role: CampRole
    scope: Scope


@dataclass(frozen=True)
class Identity:
    """The normalized auth subject. Every provider returns exactly this."""

    user_id: str                       # stable id; feeds AuditLog.author
    display_name: str                  # human label for the UI
    is_admin: bool = False             # global: create camps, manage users, edit all
    grants: tuple[Grant, ...] = ()     # normalized per-camp role grants
    is_authenticated: bool = True      # False only for ANONYMOUS

    @property
    def author(self) -> str:
        """What AuditLog stores for changes made by this subject."""
        return self.user_id


ANONYMOUS = Identity(
    user_id="", display_name="anonymous", is_admin=False, grants=(), is_authenticated=False
)


def build_identity(
    *,
    user_id: object,
    display_name: str | None = None,
    is_admin: bool = False,
    raw_grants: list[tuple[CampRole, Scope]] | None = None,
) -> Identity:
    """Build an Identity, collapsing grants to one per role: ALL wins over id-sets,
    otherwise the id-sets are unioned.
    """
    collapsed: dict[CampRole, set[int] | _All] = {}
    for role, scope in raw_grants or []:
        current = collapsed.get(role)
        if current is ALL or scope is ALL:
            collapsed[role] = ALL
        elif isinstance(scope, frozenset):
            ids = current if isinstance(current, set) else set()
            collapsed[role] = ids | scope
    grants = tuple(
        Grant(role, ALL if isinstance(value, _All) else frozenset(value))
        for role, value in collapsed.items()
    )
    return Identity(
        user_id=str(user_id),
        display_name=display_name or str(user_id),
        is_admin=is_admin,
        grants=grants,
    )


class AuthProvider(Protocol):
    """Contract every auth mode satisfies: resolve the current request to an Identity."""

    def load_identity(self) -> Identity: ...
