"""Write and read AuditLog rows.

Every change that should be traceable calls record(); the caller's service owns
the transaction, so the audit row and the change it describes commit (or roll
back) together. The log is append-only by convention — never update or delete.
list_audit() reads it back for the history views.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import g
from pydantic_core import to_jsonable_python
from sqlalchemy import and_, or_

from camp_planner.extensions import db
from camp_planner.models.audit import AuditAction, AuditLog, EntityType
from camp_planner.services import serialize

if TYPE_CHECKING:
    from camp_planner.auth.identity import Identity
    from camp_planner.models.camp import Camp

# "Camp-level" history = high-level structural changes, excluding the per-activity edits that
# belong on an activity's own history. Activities and catalog materials only when added or
# removed (not their field edits); taxonomy (categories/orgs/tags) and camp-settings changes.
# Per-activity tag *links* share the `tag` entity_type with taxonomy tag edits, so they're
# told apart by activity_id — taxonomy edits carry none, links always do.
_CAMP_LEVEL = or_(
    AuditLog.entity_type.in_([EntityType.camp, EntityType.category, EntityType.org]),
    and_(AuditLog.entity_type == EntityType.tag, AuditLog.activity_id.is_(None)),
    and_(AuditLog.entity_type == EntityType.activity,
         AuditLog.action.in_([AuditAction.create, AuditAction.delete, AuditAction.merge])),
    and_(AuditLog.entity_type == EntityType.material,
         AuditLog.action.in_([AuditAction.create, AuditAction.delete, AuditAction.merge])),
)


def record(
    *,
    camp_id: int,
    entity_type: EntityType,
    entity_id: int | None,
    action: AuditAction,
    activity_id: int | None = None,
    changes: dict | None = None,
) -> None:
    """Stage an audit row on the current session (does not commit).

    `changes` is a raw {field: [old, new]} diff; ORM values (enums, dates) are
    coerced JSON-safe here, so callers needn't.
    """
    identity: Identity = g.identity
    db.session.add(
        AuditLog(
            camp_id=camp_id,
            activity_id=activity_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            author=identity.author,
            changes=to_jsonable_python(changes) if changes else None,
        )
    )


def apply_patch(target, payload, fields) -> dict[str, list]:
    """Apply a partial pydantic patch to an ORM row, returning the audit diff.

    For each name in `fields`: if it was actually sent (in payload.model_fields_set)
    and its value differs from the row's, set it and record `{field: [old, new]}`.
    Returns the (possibly empty) diff so the caller can skip a no-op audit/commit.
    """
    changes: dict[str, list] = {}
    for field in fields:
        if field not in payload.model_fields_set:
            continue
        old, new = getattr(target, field), getattr(payload, field)
        if old != new:
            changes[field] = [old, new]
            setattr(target, field, new)
    return changes


def list_audit(
    camp: Camp, *, activity_id: int | None = None, entity_type: EntityType | None = None,
    entity_id: int | None = None, camp_level: bool = False,
    before: int | None = None, limit: int = 100,
) -> dict:
    """A page of audit entries for the camp, newest first. With no filter it's the
    whole-camp change feed; activity_id narrows to one activity's thread,
    entity_type+entity_id to a single row's history, camp_level to the high-level
    structural changes (see _CAMP_LEVEL).

    Keyset-paginated: ids are monotonic with insertion, so `id DESC` is newest-first
    and `before` (an id) fetches the next older page. Returns `next_before` — the
    cursor for the following page, or None when this was the last one."""
    query = db.select(AuditLog).filter_by(camp_id=camp.id)
    if camp_level:
        query = query.where(_CAMP_LEVEL)
    if activity_id is not None:
        query = query.filter_by(activity_id=activity_id)
    if entity_type is not None:
        query = query.filter_by(entity_type=entity_type)
    if entity_id is not None:
        query = query.filter_by(entity_id=entity_id)
    if before is not None:
        query = query.where(AuditLog.id < before)
    rows = db.session.scalars(query.order_by(AuditLog.id.desc()).limit(limit)).all()
    next_before = rows[-1].id if len(rows) == limit else None  # full page → more may follow
    return {"entries": [serialize.audit_entry(r) for r in rows], "next_before": next_before}


def _names(camp: Camp, model, name_col, ids: set[int]) -> dict[int, str]:
    """{id: name} for the camp's `model` rows in `ids` that still exist — one batched query,
    so the view can show an entity's name (and link) instead of a generic noun."""
    if not ids:
        return {}
    rows = db.session.execute(
        db.select(model.id, name_col).where(model.camp_id == camp.id, model.id.in_(ids)))
    return {row[0]: row[1] for row in rows}


def activity_titles(camp: Camp, ids: set[int]) -> dict[int, str]:
    """{id: title} of still-existing activities — links an entry's own activity (create/delete/
    merge) and labels the parent activity of per-activity detail entries (slot/todo/…)."""
    from camp_planner.models.activity import Activity

    return _names(camp, Activity, Activity.title, ids)


def material_names(camp: Camp, ids: set[int]) -> dict[int, str]:
    """{id: name} of still-existing catalog materials — links/labels an entry's own material."""
    from camp_planner.models.material import Material

    return _names(camp, Material, Material.name, ids)
