"""Material catalog (Material) + per-activity needs (MaterialNeed).

A Material is a per-camp catalog entry; a MaterialNeed links one to an activity
(amount/unit/note/ready). Catalog entries can be merged. Materials never touch the
timeline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError

from camp_planner.extensions import db
from camp_planner.models.audit import AuditAction, EntityType
from camp_planner.models.material import Material, MaterialNeed
from camp_planner.services import audit, errors, serialize

if TYPE_CHECKING:
    from camp_planner.models.activity import Activity
    from camp_planner.models.camp import Camp
    from camp_planner.schemas import (
        MaterialCreate,
        MaterialNeedAddIn,
        MaterialNeedUpdateIn,
        MaterialUpdateIn,
    )


# --- catalog (registry) ------------------------------------------------------

def list_materials(camp: Camp) -> dict:
    return {"materials": [serialize.material(m) for m in camp.materials]}


def list_materials_overview(camp: Camp) -> dict:
    """All catalog materials, each with the activity needs that use it (camp-wide
    materials page; per-unit sums are computed client-side)."""
    return {"materials": [serialize.material_overview(m) for m in camp.materials]}


def create_material(camp: Camp, payload: MaterialCreate) -> dict:
    """Create a catalog material; the uq_material_camp_norm constraint rejects a
    normalized-name duplicate within the camp (name's @validates keeps it in sync)."""
    name = payload.name.strip()
    material = Material(camp_id=camp.id, name=name, unit=payload.unit,
                        note=payload.note, url=payload.url)
    db.session.add(material)
    try:
        db.session.flush()  # assign id; a duplicate normalized_name raises here
    except IntegrityError:
        db.session.rollback()
        raise errors.Invalid(f"Materiál „{name}“ už v katalogu existuje.") from None
    audit.record(camp_id=camp.id, entity_type=EntityType.material, entity_id=material.id,
                 action=AuditAction.create, message=name)
    db.session.commit()
    return {"material": serialize.material(material)}


def update_material(material: Material, payload: MaterialUpdateIn) -> dict:
    """Update a catalog material's fields (only those sent). A rename colliding with
    another material's normalized name is rejected (uq_material_camp_norm)."""
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(material, field, value)  # name's @validates resyncs normalized_name
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise errors.Invalid(
            f"Materiál „{data.get('name', material.name)}“ už v katalogu existuje.") from None
    audit.record(camp_id=material.camp_id, entity_type=EntityType.material, entity_id=material.id,
                 action=AuditAction.update, message=material.name)
    db.session.commit()
    return {"material": serialize.material(material)}


def merge_materials(camp: Camp, source: Material, target: Material) -> dict:
    """Merge `source` into `target`: source's needs move over, then source is deleted.
    A need with no unit override keeps its effective unit (source's default is pinned
    if the defaults differ). If an activity uses both, source's amount is added to the
    existing need — but only if their effective units match; a mismatch fails the whole
    merge so the operator can align the units manually and retry."""
    if source.id == target.id:
        raise errors.Invalid("Nelze sloučit materiál sám se sebou.")

    def _unit(need, material):  # effective unit: the need's override, else the catalog default
        return need.unit if need.unit is not None else material.unit

    def _unit_phrase(unit):  # natural Czech: 'v „ks“' or 'bez jednotky'
        return f"v „{unit}“" if unit else "bez jednotky"

    target_need_by_activity = {n.activity_id: n for n in target.needs}

    # Validate before mutating, so a unit clash leaves everything untouched.
    for need in source.needs:
        existing = target_need_by_activity.get(need.activity_id)
        if existing is None or need.amount is None:
            continue  # nothing to sum into → no unit conflict possible
        su, tu = _unit(need, source), _unit(existing, target)
        if su != tu:
            raise errors.Invalid(
                f"Nelze sloučit: aktivita „{need.activity.title}“ používá „{source.name}“ "
                f"{_unit_phrase(su)} a „{target.name}“ {_unit_phrase(tu)}. "
                f"Sjednoťte jednotky a sloučení opakujte.")

    for need in list(source.needs):
        existing = target_need_by_activity.get(need.activity_id)
        if existing is not None:
            # activity already uses target (same effective unit) → sum the amounts
            if need.amount is not None:
                existing.amount = (existing.amount or 0) + need.amount
            db.session.delete(need)
            continue
        if need.unit is None and source.unit != target.unit:
            need.unit = source.unit   # keep the old effective unit
        need.material = target        # reassign across the relationship

    db.session.delete(source)
    audit.record(camp_id=camp.id, entity_type=EntityType.material, entity_id=target.id,
                 action=AuditAction.update, message=f"sloučeno z „{source.name}“")
    db.session.commit()
    return {"material": serialize.material(target)}


def delete_material(material: Material) -> dict:
    """Delete a catalog material. Refused while any activity still uses it (merge it or
    remove those needs first), so a delete never silently drops usages."""
    if material.needs:
        raise errors.Invalid(
            f"Materiál „{material.name}“ nelze smazat – používají ho aktivity. "
            f"Nejprve ho slučte s jiným, nebo ho odeberte z aktivit.")
    camp_id, material_id, name = material.camp_id, material.id, material.name
    db.session.delete(material)
    audit.record(camp_id=camp_id, entity_type=EntityType.material, entity_id=material_id,
                 action=AuditAction.delete, message=name)
    db.session.commit()
    return {"id": material_id}


# --- need (per activity) -----------------------------------------------------

def add_need(activity: Activity, payload: MaterialNeedAddIn) -> dict:
    material = next((m for m in activity.camp.materials if m.id == payload.material_id), None)
    if material is None:
        raise errors.Invalid("Materiál: neznámá položka katalogu.")
    if any(n.material_id == material.id for n in activity.material_needs):
        raise errors.Invalid("Tento materiál už je u akce přidán.")

    need = MaterialNeed(activity_id=activity.id, material_id=material.id,
                        amount=payload.amount, unit=payload.unit,
                        note=payload.note, is_ready=payload.is_ready)
    db.session.add(need)
    db.session.flush()
    audit.record(camp_id=activity.camp_id, activity_id=activity.id, entity_type=EntityType.material_need,
                 entity_id=need.id, action=AuditAction.create, message=material.name)
    db.session.commit()
    return {"need": serialize.material_need(need)}


def update_need(need: MaterialNeed, payload: MaterialNeedUpdateIn) -> dict:
    for field in ("amount", "unit", "note", "is_ready"):
        if field in payload.model_fields_set:
            setattr(need, field, getattr(payload, field))
    audit.record(camp_id=need.activity.camp_id, activity_id=need.activity_id,
                 entity_type=EntityType.material_need, entity_id=need.id, action=AuditAction.update)
    db.session.commit()
    return {"need": serialize.material_need(need)}


def delete_need(need: MaterialNeed) -> dict:
    need_id, activity = need.id, need.activity
    db.session.delete(need)
    audit.record(camp_id=activity.camp_id, activity_id=activity.id, entity_type=EntityType.material_need,
                 entity_id=need_id, action=AuditAction.delete)
    db.session.commit()
    return {"id": need_id}
