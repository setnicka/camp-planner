"""Resolve camp slugs to camp ids.

External identity sources (proxy headers, embedded callback) reference camps by
slug — the stable identifier a host knows, decoupled from our internal ids. This
maps those slugs to the ids the permission model uses. (The standalone DB and CLI
are already id-based and don't need it.)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from camp_planner.auth.identity import ALL
from camp_planner.extensions import db
from camp_planner.models.camp import Camp

if TYPE_CHECKING:
    from camp_planner.auth.identity import CampRole, Scope


def resolve_slug_grants(
    grants: list[tuple[CampRole, set[str] | None]],
) -> list[tuple[CampRole, Scope]]:
    """Map each grant's camp slugs to ids in one query. A None scope means all camps
    (ALL); unknown/not-yet-created slugs are dropped."""
    slugs = {slug for _, scope in grants if scope is not None for slug in scope}
    slug_to_id: dict[str, int] = (
        dict(db.session.execute(db.select(Camp.slug, Camp.id).where(Camp.slug.in_(slugs))).all())
        if slugs
        else {}
    )
    return [
        (role, ALL if scope is None else frozenset(slug_to_id[s] for s in scope if s in slug_to_id))
        for role, scope in grants
    ]
