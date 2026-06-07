"""Permission resolution: pure helpers over an Identity, plus view decorators.

Rules, per (user, camp):
  admin           -> everything (incl. camp name/slug, create camps, manage users)
  editor (scope)  -> view + edit within the camp, but NOT its name/slug
  viewer (scope)  -> view within the camp

The can_* helpers default to the current request's g.identity but accept
an explicit identity= so they're unit-testable without a request context.
"""

from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING, Callable

from flask import abort, current_app, g, redirect, request, url_for

from camp_planner.auth.identity import ALL, CampRole

if TYPE_CHECKING:
    from collections.abc import Container

    from camp_planner.auth.identity import Identity, Scope
    from camp_planner.models.camp import Camp


def _identity(explicit: Identity | None) -> Identity:
    return explicit if explicit is not None else g.identity


def _camp_id(camp: Camp | int) -> int:
    return camp if isinstance(camp, int) else camp.id


def _scope_covers(scope: Scope, camp_id: int) -> bool:
    return scope is ALL or camp_id in scope  # type: ignore[operator]


def _has_role(identity: Identity, accepted: Container[CampRole], camp_id: int) -> bool:
    return any(
        grant.role in accepted and _scope_covers(grant.scope, camp_id)
        for grant in identity.grants
    )


def can_view(camp: Camp | int, identity: Identity | None = None) -> bool:
    who = _identity(identity)
    # editor implies viewer, so either role grants view.
    return who.is_admin or _has_role(who, (CampRole.editor, CampRole.viewer), _camp_id(camp))


def can_edit(camp: Camp | int, identity: Identity | None = None) -> bool:
    who = _identity(identity)
    return who.is_admin or _has_role(who, (CampRole.editor,), _camp_id(camp))


def can_edit_camp_meta(camp: Camp | int | None = None, identity: Identity | None = None) -> bool:
    """Editing a camp's name/slug is admin-only, even for editors."""
    return _identity(identity).is_admin


def can_create_camp(identity: Identity | None = None) -> bool:
    return _identity(identity).is_admin


def can_manage_users(identity: Identity | None = None) -> bool:
    return _identity(identity).is_admin


# --- view decorators ---------------------------------------------------------

def _resolve_camp(view_kwargs: dict[str, object]) -> Camp:
    """Look up the camp a decorated view operates on, from its route kwargs."""
    from camp_planner.extensions import db
    from camp_planner.models.camp import Camp

    if "camp_id" in view_kwargs:
        return db.get_or_404(Camp, view_kwargs["camp_id"])
    if "slug" in view_kwargs:
        return db.first_or_404(db.select(Camp).filter_by(slug=view_kwargs["slug"]))
    abort(500)  # decorator applied to a view without a camp_id/slug param


def _deny():
    """Anonymous -> login (if any) else 401; authenticated-but-forbidden -> 403."""
    if not g.identity.is_authenticated:
        endpoint = current_app.config.get("AUTH_LOGIN_ENDPOINT")
        if endpoint:
            return redirect(url_for(endpoint, next=request.url))
        abort(401)
    abort(403)


def _camp_guard(check: Callable[[Camp], bool]) -> Callable:
    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not check(_resolve_camp(kwargs)):
                return _deny()
            return view(*args, **kwargs)

        return wrapper

    return decorator


require_view = _camp_guard(can_view)
require_edit = _camp_guard(can_edit)


def require_admin(view: Callable) -> Callable:
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not g.identity.is_admin:
            return _deny()
        return view(*args, **kwargs)

    return wrapper
