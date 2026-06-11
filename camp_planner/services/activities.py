"""Activity mutations: create / update / delete, plus the org-assignment and
tag set-replacement operations used by the activity detail page.

Bodies are validated by the request schemas; what remains here is the business
validation that needs the DB (a category/org/tag must belong to this camp) — raised
as errors.Invalid — and the writes themselves. Each function owns its transaction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from camp_planner.extensions import db
from camp_planner.models.activity import Activity, ActivityAssignment, ActivityTag
from camp_planner.models.audit import AuditAction, EntityType
from camp_planner.models.camp import TagKind
from camp_planner.services import audit, errors, serialize, timeline

if TYPE_CHECKING:
    from camp_planner.models.camp import Camp
    from camp_planner.schemas import ActivityCreate, ActivityOrgsIn, ActivityUpdate, TagsIn, TagValueUpdate

# Fields a PATCH may change, in audit-diff order.
_EDITABLE = ("title", "category_id", "type", "description_md", "config")


def _check_category(camp: Camp, category_id: int | None) -> None:
    if category_id is not None and category_id not in {c.id for c in camp.categories}:
        raise errors.Invalid("Kategorie: neznámá kategorie této akce.")


def create_activity(camp: Camp, payload: ActivityCreate) -> dict:
    _check_category(camp, payload.category_id)
    activity = Activity(camp_id=camp.id, title=payload.title, category_id=payload.category_id,
                        type=payload.type, description_md=payload.description_md, config=payload.config)
    db.session.add(activity)
    db.session.flush()
    audit.record(camp_id=camp.id, activity_id=activity.id, entity_type=EntityType.activity,
                 entity_id=activity.id, action=AuditAction.create,
                 changes={"title": [None, payload.title]})
    db.session.commit()
    return {"activity": serialize.activity(activity)}


def update_activity(activity: Activity, payload: ActivityUpdate) -> dict:
    if "category_id" in payload.model_fields_set:
        _check_category(activity.camp, payload.category_id)

    changes: dict[str, list] = {}
    for field in _EDITABLE:
        if field not in payload.model_fields_set:
            continue
        old, new = getattr(activity, field), getattr(payload, field)
        if old != new:
            changes[field] = [old, new]
            setattr(activity, field, new)

    if changes:
        audit.record(camp_id=activity.camp_id, activity_id=activity.id, entity_type=EntityType.activity,
                     entity_id=activity.id, action=AuditAction.update, changes=changes)
        db.session.commit()
    return {"activity": serialize.activity(activity)}


def delete_activity(activity: Activity) -> dict:
    """Delete an activity. Refused (400) while it still has slots on the timeline — remove
    those first (or merge the activity), so a delete never silently drops placed slots."""
    if activity.slots:
        raise errors.Invalid(
            f"Aktivitu „{activity.title}“ nelze smazat – má naplánované sloty. "
            f"Nejprve je odeber z timeline, nebo aktivitu sluč s jinou.")
    activity_id, camp_id, title = activity.id, activity.camp_id, activity.title
    db.session.delete(activity)
    audit.record(camp_id=camp_id, activity_id=None, entity_type=EntityType.activity,
                 entity_id=activity_id, action=AuditAction.delete,
                 message=f"smazána akce „{title}“")
    db.session.commit()
    return {"id": activity_id}


def merge_activities(source: Activity, target: Activity) -> dict:
    """Merge `source` INTO `target`, then delete `source`. Its todos and slots are reassigned
    to the target; its material needs are joined into the target's (a need for a material the
    target already uses sums the amounts — but only if their effective units match, else the
    whole merge fails); the source's org assignments and tags are dropped (not transferred).
    Bumps timeline_rev because slots change which activity they belong to."""
    if source.id == target.id:
        raise errors.Invalid("Nelze sloučit aktivitu se sebou samou.")
    if source.camp_id != target.camp_id:
        raise errors.Invalid("Nelze sloučit aktivity z různých akcí.")

    def _unit(need):  # effective unit: the need's override, else the catalog default
        return need.unit if need.unit is not None else need.material.unit

    # pre-check unit conflicts on materials both activities use, before mutating anything
    target_need_by_material = {n.material_id: n for n in target.material_needs}
    for need in source.material_needs:
        existing = target_need_by_material.get(need.material_id)
        if existing is None or need.amount is None:
            continue  # nothing to sum into → no unit conflict possible
        if _unit(need) != _unit(existing):
            raise errors.Invalid(
                f"Nelze sloučit: materiál „{need.material.name}“ má v obou aktivitách "
                f"různé jednotky. Nejprve je sjednoť.")

    for todo in list(source.todos):
        todo.activity = target
    for slot in list(source.slots):
        slot.activity = target
    for need in list(source.material_needs):
        existing = target_need_by_material.get(need.material_id)
        if existing is not None:
            if need.amount is not None:
                existing.amount = (existing.amount or 0) + need.amount
            source.material_needs.remove(need)   # orphan → deleted once, not re-deleted by source's cascade
        else:
            need.activity = target  # reassign across the relationship (keeps its unit override)

    # source's org assignments + tags are dropped with the source (cascade delete-orphan)
    source_title = source.title
    db.session.delete(source)
    timeline.bump_timeline_rev(target.camp)
    audit.record(camp_id=target.camp_id, activity_id=target.id, entity_type=EntityType.activity,
                 entity_id=target.id, action=AuditAction.update, changes={"merged_from": [source_title, None]})
    db.session.commit()
    return {"activity": serialize.activity(target)}


def set_orgs(activity: Activity, payload: ActivityOrgsIn) -> dict:
    """Replace the activity's garant/helper orgs with the submitted set."""
    org_ids = {o.id for o in activity.camp.orgs}
    new: list[ActivityAssignment] = []
    for item in payload.orgs:  # schema already rejected (org_id, role) duplicates
        if item.org_id not in org_ids:
            raise errors.Invalid("Orgové: neznámý org této akce.")
        new.append(ActivityAssignment(org_id=item.org_id, role=item.role))

    activity.assignments = new  # delete-orphan removes the previous rows
    audit.record(camp_id=activity.camp_id, activity_id=activity.id, entity_type=EntityType.assignment,
                 entity_id=None, action=AuditAction.update, message="orgové akce uloženi")
    db.session.commit()
    return {"orgs": [serialize.assignment(a) for a in activity.assignments]}


def _normalize_tag_value(kind: TagKind, value: str | None) -> str | None:
    """Coerce/validate a tag's per-activity value against its kind. A label carries no
    value; check is true/false; progress is 0–100; text is free. Blank clears it."""
    if kind is TagKind.label:
        if value not in (None, ""):
            raise errors.Invalid("Štítek nemá hodnotu.")
        return None
    if value in (None, ""):
        return None
    if kind is TagKind.check:
        if value not in ("true", "false"):
            raise errors.Invalid("Hodnota typu „Hotovo / ne“ musí být true/false.")
        return value
    if kind is TagKind.progress:
        try:
            number = int(value)
        except ValueError:
            raise errors.Invalid("Postup musí být celé číslo 0–100.") from None
        if not 0 <= number <= 100:
            raise errors.Invalid("Postup musí být 0–100.")
        return str(number)
    return value  # text: free-form


def set_tags(activity: Activity, payload: TagsIn) -> dict:
    """Replace the activity's tags (with per-tag value) with the submitted set."""
    tags_by_id = {t.id: t for t in activity.camp.tags}
    new: list[ActivityTag] = []
    for item in payload.tags:  # schema already rejected duplicate tag_ids
        tag = tags_by_id.get(item.tag_id)
        if tag is None:
            raise errors.Invalid("Tagy: neznámý tag této akce.")
        new.append(ActivityTag(tag_id=item.tag_id,
                               value=_normalize_tag_value(tag.kind, item.value)))

    activity.tags = new  # delete-orphan removes the previous links
    audit.record(camp_id=activity.camp_id, activity_id=activity.id, entity_type=EntityType.tag,
                 entity_id=None, action=AuditAction.update, message="tagy akce uloženy")
    db.session.commit()
    return {"tags": [serialize.tag_link(t) for t in activity.tags]}


def set_tag_value(link: ActivityTag, payload: TagValueUpdate) -> dict:
    """Update a single applied tag's value (validated against the tag's kind)."""
    link.value = _normalize_tag_value(link.tag.kind, payload.value)
    audit.record(camp_id=link.activity.camp_id, activity_id=link.activity_id, entity_type=EntityType.tag,
                 entity_id=link.tag_id, action=AuditAction.update, message="hodnota tagu")
    db.session.commit()
    return {"tag": serialize.tag_link(link)}
