"""Todo mutations for an activity (create / update / delete; done is toggled via update).

Takes the validated request schema (the view layer validated the body), so there's
no parsing here; the response is built by serialize.py. Todos don't affect the
timeline, so none of this bumps timeline_rev. Each function owns its transaction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from camp_planner.extensions import db
from camp_planner.models.activity import Todo
from camp_planner.models.audit import AuditAction, EntityType
from camp_planner.services import audit, serialize

if TYPE_CHECKING:
    from camp_planner.models.activity import Activity
    from camp_planner.models.camp import Camp
    from camp_planner.schemas import TodoCreate, TodoUpdate

# Fields a PATCH may change (audit-diff order).
_EDITABLE = ("title", "note", "due_date", "is_done")


def list_todos_overview(camp: Camp) -> dict:
    """Every todo across the camp's activities (camp-wide TODO page), each carrying its
    activity for grouping/filtering; ordering/filtering is done client-side."""
    return {"todos": [serialize.todo_overview(t) for a in camp.activities for t in a.todos]}


def create_todo(activity: Activity, payload: TodoCreate) -> dict:
    todo = Todo(activity_id=activity.id, title=payload.title, note=payload.note,
                due_date=payload.due_date, is_done=payload.is_done)
    db.session.add(todo)
    db.session.flush()
    audit.record(camp_id=activity.camp_id, activity_id=activity.id, entity_type=EntityType.todo,
                 entity_id=todo.id, action=AuditAction.create, changes={"title": [None, todo.title]})
    db.session.commit()
    return {"todo": serialize.todo(todo)}


def update_todo(todo: Todo, payload: TodoUpdate) -> dict:
    changes = audit.apply_patch(todo, payload, _EDITABLE)
    if changes:
        audit.record(camp_id=todo.activity.camp_id, activity_id=todo.activity_id, entity_type=EntityType.todo,
                     entity_id=todo.id, action=AuditAction.update, changes=changes)
        db.session.commit()
    return {"todo": serialize.todo(todo)}


def delete_todo(todo: Todo) -> dict:
    todo_id, activity, title = todo.id, todo.activity, todo.title
    db.session.delete(todo)
    audit.record(camp_id=activity.camp_id, activity_id=activity.id, entity_type=EntityType.todo,
                 entity_id=todo_id, action=AuditAction.delete, changes={"title": [title, None]})
    db.session.commit()
    return {"id": todo_id}
