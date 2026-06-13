"""ORM → response-model adapters.

Builds the pydantic response models from schemas.py out of the ORM rows — handling
the relational flattening (a slot's org initials, a material's catalog name, …) that
from_attributes can't express — and returns them as JSON-ready dicts for the API
envelopes. The shape itself is defined once, in schemas.py.

Each row shape has a private model-builder (_slot, _assignment, …) returning
the pydantic model, reused both standalone and nested inside activity(); the public
functions just _dump the model to a JSON-ready dict at the API boundary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from camp_planner.models.activity import OrgRole
from camp_planner.models.common import czech_sort_key
from camp_planner.models.slot import SlotRole
from camp_planner.schemas import (
    ActivityOut,
    AssignmentOut,
    AuditEntryOut,
    CampOut,
    MaterialNeedOut,
    MaterialOut,
    MaterialUsageOut,
    MaterialWithUsagesOut,
    SlotOrgOut,
    SlotOut,
    TagLinkOut,
    TodoOut,
    TodoWithActivityOut,
)

if TYPE_CHECKING:
    from camp_planner.models.activity import Activity, ActivityAssignment, ActivityTag, Todo
    from camp_planner.models.audit import AuditLog
    from camp_planner.models.camp import Camp
    from camp_planner.models.material import Material, MaterialNeed
    from camp_planner.models.slot import Slot


def _dump(model) -> dict:
    return model.model_dump(mode="json")


# --- model builders (reused standalone and nested inside activity) -----------

def _slot_orgs(s: Slot) -> list[SlotOrgOut]:
    return [SlotOrgOut(org_id=a.org_id, initials=a.org.initials) for a in s.assignments]


def _slot(s: Slot) -> SlotOut:
    return SlotOut(
        id=s.id, activity_id=s.activity_id, role=s.role,
        start_at=s.start_at, end_at=s.end_at, orgs=_slot_orgs(s),
    )


def _assignment(a: ActivityAssignment) -> AssignmentOut:
    return AssignmentOut(org_id=a.org_id, initials=a.org.initials, role=a.role)


def _tag_link(t: ActivityTag) -> TagLinkOut:
    return TagLinkOut(tag_id=t.tag_id, name=t.tag.name, kind=t.tag.kind,
                      pinned=t.tag.pinned, value=t.value)


def _material_need(m: MaterialNeed) -> MaterialNeedOut:
    """An activity's material need, with the catalog material nested."""
    return MaterialNeedOut(id=m.id, amount=m.amount, unit=m.unit, note=m.note,
                           is_ready=m.is_ready, material=m.material)


# --- public adapters (dump the model at the API boundary) --------------------

def slot(s: Slot) -> dict:
    return _dump(_slot(s))


def slot_orgs(s: Slot) -> list[dict]:
    return [_dump(o) for o in _slot_orgs(s)]


def assignment(a: ActivityAssignment) -> dict:
    return _dump(_assignment(a))


def tag_link(t: ActivityTag) -> dict:
    return _dump(_tag_link(t))


def todo(t: Todo) -> dict:
    return _dump(TodoOut.model_validate(t))


def todo_overview(t: Todo) -> dict:
    """A todo with its activity, for the camp-wide TODO page."""
    return _dump(TodoWithActivityOut(
        id=t.id, activity_id=t.activity_id, activity_title=t.activity.title,
        title=t.title, note=t.note, due_date=t.due_date, is_done=t.is_done))


def material(m: Material) -> dict:
    """A catalog material (registry item)."""
    return _dump(MaterialOut.model_validate(m))


def material_need(m: MaterialNeed) -> dict:
    return _dump(_material_need(m))


def camp(c: Camp) -> dict:
    return _dump(CampOut.model_validate(c))


def audit_entry(row: AuditLog) -> dict:
    return _dump(AuditEntryOut.model_validate(row))


def material_overview(m: Material) -> dict:
    """A catalog material with every activity need that uses it (camp materials page)."""
    return _dump(MaterialWithUsagesOut(
        id=m.id, name=m.name, unit=m.unit, note=m.note, url=m.url,
        usages=[MaterialUsageOut(
            need_id=n.id, activity_id=n.activity_id, activity_title=n.activity.title,
            amount=n.amount, unit=n.unit, note=n.note, is_ready=n.is_ready)
            for n in m.needs],
    ))


def activity_overview(a: Activity) -> dict:
    """A compact activity row for the camp-wide overview / status page: category, garant/helper
    initials (czech-sorted), todo/material progress counts, slot counts by role, and the applied
    tags as {tag_id: value} (a present key = the tag applies; its value may be null). Filtering
    and sorting are done client-side from this shape."""
    garants = sorted((x.org.initials for x in a.assignments if x.role is OrgRole.garant), key=czech_sort_key)
    helpers = sorted((x.org.initials for x in a.assignments if x.role is OrgRole.helper), key=czech_sort_key)
    return {
        "id": a.id,
        "title": a.title,
        "category": {"id": a.category_id, "label": a.category.label, "color": a.category.color} if a.category else None,
        "garants": garants,
        "helpers": helpers,
        "org_ids": sorted({x.org_id for x in a.assignments}),
        "garant_ids": sorted({x.org_id for x in a.assignments if x.role is OrgRole.garant}),
        "todos": {"done": sum(t.is_done for t in a.todos), "total": len(a.todos)},
        "materials": {"done": sum(n.is_ready for n in a.material_needs), "total": len(a.material_needs)},
        "slots": {role.value: sum(s.role is role for s in a.slots) for role in SlotRole},
        "tags": {str(at.tag_id): at.value for at in a.tags},
    }


def activity(a: Activity) -> dict:
    return _dump(ActivityOut(
        id=a.id, camp_id=a.camp_id, title=a.title, type=a.type,
        category_id=a.category_id, description_md=a.description_md, config=a.config,
        slots=[_slot(s) for s in a.slots],
        orgs=[_assignment(x) for x in a.assignments],
        tags=[_tag_link(t) for t in a.tags],
        todos=[TodoOut.model_validate(t) for t in a.todos],
        material_needs=[_material_need(m) for m in a.material_needs],
    ))
