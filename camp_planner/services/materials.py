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
from camp_planner.models.common import czech_sort_key
from camp_planner.models.material import Material, MaterialAssignment, MaterialNeed, SumStrategy
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
                 action=AuditAction.create, changes={"name": [None, name]})
    db.session.commit()
    return {"material": serialize.material(material)}


_EDITABLE = ("name", "unit", "note", "url")  # name's @validates resyncs normalized_name


def _set_orgs(material: Material, org_ids: list[int]) -> list | None:
    """Replace the material's responsible orgs with `org_ids` (validated against the camp
    roster). Returns the audit diff [before, after] (czech-sorted initials) when it changed,
    else None — unchanged means no reassignment (avoids delete-orphan churn) and no audit."""
    initials = {o.id: o.initials for o in material.camp.orgs}
    for oid in org_ids:
        if oid not in initials:
            raise errors.Invalid("Orgové: neznámý org této akce.")
    before = sorted((a.org.initials for a in material.assignments), key=czech_sort_key)
    after = sorted((initials[oid] for oid in org_ids), key=czech_sort_key)
    if before == after:
        return None
    material.assignments = [MaterialAssignment(org_id=oid) for oid in org_ids]
    return [before, after]


def _set_labels(material: Material, labels: list[str]) -> list | None:
    """Replace the material's acquisition labels (already cleaned/deduped by the schema).
    Returns the audit diff [before, after] when it changed, else None."""
    before = material.acquisition_labels or []
    if before == labels:
        return None
    material.acquisition_labels = labels
    return [before, labels]


def _set_strategy(material: Material, strategy: SumStrategy) -> list | None:
    """Set the amount-aggregation strategy. Handled apart from apply_patch because the column
    is NOT NULL — a sent null must stay 'unchanged', not clear it. Returns the diff or None."""
    if material.sum_strategy == strategy:
        return None
    before = material.sum_strategy
    material.sum_strategy = strategy
    return [before, strategy]


def update_material(material: Material, payload: MaterialUpdateIn) -> dict:
    """Update a catalog material's fields (only those sent). A rename colliding with
    another material's normalized name is rejected (uq_material_camp_norm)."""
    changes = audit.apply_patch(material, payload, _EDITABLE)
    # The list/enum fields below are applied apart from apply_patch so a sent null reads as
    # "unchanged" (not "clear"); each helper returns its audit diff [before, after] or None.
    if payload.acquisition_labels is not None:
        labels_diff = _set_labels(material, payload.acquisition_labels)
        if labels_diff:
            changes["acquisition_labels"] = labels_diff
    if payload.sum_strategy is not None:
        strat_diff = _set_strategy(material, payload.sum_strategy)
        if strat_diff:
            changes["sum_strategy"] = strat_diff
    if payload.org_ids is not None:
        orgs_diff = _set_orgs(material, payload.org_ids)
        if orgs_diff:
            changes["orgs"] = orgs_diff
    if not changes:
        return {"material": serialize.material(material)}
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise errors.Invalid(f"Materiál „{material.name}“ už v katalogu existuje.") from None
    audit.record(camp_id=material.camp_id, entity_type=EntityType.material, entity_id=material.id,
                 action=AuditAction.update, changes=changes)
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

    # carry the source's acquisition labels (union, dedupe) and responsible orgs onto the target,
    # so a dedup merge doesn't silently drop them; source's duplicates fall away with the source.
    tgt_labels = target.acquisition_labels or []
    extra = [lab for lab in (source.acquisition_labels or []) if lab not in tgt_labels]
    if extra:
        target.acquisition_labels = tgt_labels + extra
    tgt_org_ids = {a.org_id for a in target.assignments}
    for a in list(source.assignments):
        if a.org_id not in tgt_org_ids:
            a.material = target
            tgt_org_ids.add(a.org_id)

    db.session.delete(source)
    audit.record(camp_id=camp.id, entity_type=EntityType.material, entity_id=target.id,
                 action=AuditAction.merge, changes={"merged_from": [source.name, None]})
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
                 action=AuditAction.delete, changes={"name": [name, None]})
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
                 entity_id=need.id, action=AuditAction.create, changes={"material": [None, material.name]})
    db.session.commit()
    return {"need": serialize.material_need(need)}


def update_need(need: MaterialNeed, payload: MaterialNeedUpdateIn) -> dict:
    changes = audit.apply_patch(need, payload, ("amount", "unit", "note", "is_ready"))
    if changes:
        audit.record(camp_id=need.activity.camp_id, activity_id=need.activity_id,
                     entity_type=EntityType.material_need, entity_id=need.id,
                     action=AuditAction.update, changes=changes)
        db.session.commit()
    return {"need": serialize.material_need(need)}


def delete_need(need: MaterialNeed) -> dict:
    need_id, activity, name = need.id, need.activity, need.material.name
    db.session.delete(need)
    audit.record(camp_id=activity.camp_id, activity_id=activity.id, entity_type=EntityType.material_need,
                 entity_id=need_id, action=AuditAction.delete, changes={"material": [name, None]})
    db.session.commit()
    return {"id": need_id}
