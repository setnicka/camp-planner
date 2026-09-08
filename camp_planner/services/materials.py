"""Material catalog (Material) + per-activity needs (MaterialNeed).

A Material is a per-camp catalog entry; a MaterialNeed links one to an activity
(amount/unit/note/ready). Catalog entries can be merged. Materials never touch the
timeline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError

from camp_planner.extensions import db, db_session
from camp_planner.models.audit import AuditAction, EntityType
from camp_planner.models.common import by_name
from camp_planner.models.material import Material, MaterialAssignment, MaterialNeed
from camp_planner.services import audit, errors, inventory, orgs, serialize

if TYPE_CHECKING:
    from camp_planner.models.activity import Activity
    from camp_planner.models.camp import Camp
    from camp_planner.models.inventory import InventoryItem
    from camp_planner.schemas import (
        MaterialCreate,
        MaterialNeedAddIn,
        MaterialNeedUpdateIn,
        MaterialUpdateIn,
    )


# --- catalog (registry) ------------------------------------------------------

def list_materials(camp: Camp) -> dict:
    return {"materials": [serialize.material(m) for m in by_name(camp.materials)]}


def list_materials_overview(camp: Camp) -> dict:
    """All catalog materials, each with the activity needs that use it (camp-wide
    materials page; per-unit sums are computed client-side, see Material.unit_totals for
    the same rule where the server needs it)."""
    return {"materials": [serialize.material_overview(m) for m in by_name(camp.materials)]}


# --- the warehouse link ------------------------------------------------------

def _refuse_taken(camp_id: int, item: InventoryItem, material: Material | None = None) -> None:
    """One material per thing within a camp (uq_material_camp_item), said in words."""
    other = db_session.scalar(
        db.select(Material).filter_by(camp_id=camp_id, inventory_item_id=item.id))
    if other is not None and other is not material:
        raise errors.Invalid(f"Na věc „{item.name}“ už odkazuje materiál „{other.name}“.")


def _commit_or_refuse(message: str) -> None:
    """Commit, or say in words what the unique constraint refused: _refuse_taken only sees
    what this session knows, two people linking one thing race past it."""
    try:
        db_session.commit()
    except IntegrityError:
        db_session.rollback()
        raise errors.Invalid(message) from None


def _relink(material: Material, item_id: int | None) -> dict[str, list]:
    """Point the material at another thing, or at none. The diff names the things."""
    if item_id == material.inventory_item_id:
        return {}
    item = inventory.live_item(item_id) if item_id is not None else None
    if item is not None:
        _refuse_taken(material.camp_id, item, material)
    old = material.inventory_item.name if material.inventory_item else None
    material.inventory_item = item   # the object, so the response carries the new link
    return {"inventory_item": [old, item.name if item else None]}


def create_material(camp: Camp, payload: MaterialCreate) -> dict:
    """Create a catalog material; the uq_material_camp_norm constraint rejects a
    normalized-name duplicate within the camp (name's @validates keeps it in sync).

    With inventory_item_id the material is made from a warehouse thing: its name, unit and
    url stand in for what the caller did not send. A same-named unlinked material is linked
    instead of duplicated, otherwise unchanged."""
    item = inventory.live_item(payload.inventory_item_id) if payload.inventory_item_id is not None else None
    name = (payload.name or "").strip() or (item.name.strip() if item is not None else "")
    if not name:
        raise errors.Invalid("Název je povinný.")
    if item is not None:
        existing = db_session.scalar(db.select(Material).filter_by(
            camp_id=camp.id, normalized_name=Material.normalize_name(name)))
        if existing is not None:
            if existing.inventory_item_id not in (None, item.id):
                raise errors.Invalid(
                    f"Materiál „{existing.name}“ už existuje a odkazuje na jinou věc ve skladu.")
            if changes := _relink(existing, item.id):
                audit.record(camp_id=camp.id, entity_type=EntityType.material,
                             entity_id=existing.id, action=AuditAction.update, changes=changes)
                _commit_or_refuse(f"Na věc „{item.name}“ už odkazuje jiný materiál.")
            return {"material": serialize.material(existing)}
        _refuse_taken(camp.id, item)
    material = Material(camp_id=camp.id, name=name, note=payload.note,
                        unit=payload.unit if item is None else payload.unit or item.unit,
                        url=payload.url if item is None else payload.url or item.url,
                        inventory_item=item)
    db_session.add(material)
    try:
        db_session.flush()  # assign id; a duplicate normalized_name raises here
    except IntegrityError:
        db_session.rollback()
        raise errors.Invalid(f"Materiál „{name}“ už v katalogu existuje.") from None
    changes = {"name": [None, name]}
    if item is not None:
        changes["inventory_item"] = [None, item.name]
    audit.record(camp_id=camp.id, entity_type=EntityType.material, entity_id=material.id,
                 action=AuditAction.create, changes=changes)
    db_session.commit()
    return {"material": serialize.material(material)}


_EDITABLE = ("name", "unit", "note", "url")  # name's @validates resyncs normalized_name


def update_material(material: Material, payload: MaterialUpdateIn) -> dict:
    """Update a catalog material's fields (only those sent). A rename colliding with
    another material's normalized name is rejected (uq_material_camp_norm)."""
    changes = audit.apply_patch(material, payload, _EDITABLE)
    # Applied apart from apply_patch: a sent null means "unchanged" for these fields.
    for field in ("acquisition_labels", "sum_strategy"):
        value = getattr(payload, field)
        if value is not None:
            changes.update(audit.apply_changes(material, {field: value}))
    if payload.org_ids is not None:
        orgs_diff = orgs.replace_assignments(
            material, material.camp, payload.org_ids, MaterialAssignment)
        if orgs_diff:
            changes["orgs"] = orgs_diff
    if "inventory_item_id" in payload.model_fields_set:
        # no_autoflush: the lookups inside would flush a pending rename, and its unique
        # violation would then escape the guarded flush below as a 500.
        with db_session.no_autoflush:
            changes.update(_relink(material, payload.inventory_item_id))
    if not changes:
        return {"material": serialize.material(material)}
    try:
        db_session.flush()
    except IntegrityError:
        db_session.rollback()
        raise errors.Invalid(f"Materiál „{material.name}“ už v katalogu existuje.") from None
    audit.record(camp_id=material.camp_id, entity_type=EntityType.material, entity_id=material.id,
                 action=AuditAction.update, changes=changes)
    db_session.commit()
    return {"material": serialize.material(material)}


def merge_materials(camp: Camp, source: Material, target: Material) -> dict:
    """Merge `source` into `target`: source's needs move over, then source is deleted.
    A need with no unit override keeps its effective unit (source's default is pinned
    if the defaults differ). If an activity uses both, source's amount is added to the
    existing need — but only if their effective units match; a mismatch fails the whole
    merge so the operator can align the units manually and retry."""
    if source.id == target.id:
        raise errors.Invalid("Nelze sloučit materiál sám se sebou.")

    def _unit_phrase(unit):  # natural Czech: 'v „ks“' or 'bez jednotky'
        return f"v „{unit}“" if unit else "bez jednotky"

    target_need_by_activity = {n.activity_id: n for n in target.needs}

    # Validate before mutating, so a unit clash leaves everything untouched.
    for need in source.needs:
        existing = target_need_by_activity.get(need.activity_id)
        if existing is None or need.amount is None:
            continue  # nothing to sum into → no unit conflict possible
        su, tu = need.effective_unit, existing.effective_unit
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
            db_session.delete(need)
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
    # The target keeps its own thing; without one it inherits the source's. The pair is
    # unique per camp, so the source has to let go before the target takes it.
    changes = {"merged_from": [source.name, None]}
    if target.inventory_item_id is None and source.inventory_item_id is not None:
        item = source.inventory_item
        source.inventory_item = None
        db_session.flush()
        target.inventory_item = item
        changes["inventory_item"] = [None, item.name]

    db_session.delete(source)
    audit.record(camp_id=camp.id, entity_type=EntityType.material, entity_id=target.id,
                 action=AuditAction.merge, changes=changes)
    db_session.commit()
    return {"material": serialize.material(target)}


def delete_material(material: Material) -> dict:
    """Delete a catalog material. Refused while any activity still uses it (merge it or
    remove those needs first), so a delete never silently drops usages."""
    if material.needs:
        raise errors.Invalid(
            f"Materiál „{material.name}“ nelze smazat – používají ho aktivity. "
            f"Nejprve ho slučte s jiným, nebo ho odeberte z aktivit.")
    camp_id, material_id, name = material.camp_id, material.id, material.name
    db_session.delete(material)
    audit.record(camp_id=camp_id, entity_type=EntityType.material, entity_id=material_id,
                 action=AuditAction.delete, changes={"name": [name, None]})
    db_session.commit()
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
    db_session.add(need)
    db_session.flush()
    audit.record(camp_id=activity.camp_id, activity_id=activity.id, entity_type=EntityType.material_need,
                 entity_id=need.id, action=AuditAction.create, changes={"material": [None, material.name]})
    db_session.commit()
    return {"need": serialize.material_need(need)}


def update_need(need: MaterialNeed, payload: MaterialNeedUpdateIn) -> dict:
    changes = audit.apply_patch(need, payload, ("amount", "unit", "note", "is_ready"))
    if changes:
        audit.record(camp_id=need.activity.camp_id, activity_id=need.activity_id,
                     entity_type=EntityType.material_need, entity_id=need.id,
                     action=AuditAction.update, changes=changes)
        db_session.commit()
    return {"need": serialize.material_need(need)}


def delete_need(need: MaterialNeed) -> dict:
    need_id, activity, name = need.id, need.activity, need.material.name
    db_session.delete(need)
    audit.record(camp_id=activity.camp_id, activity_id=activity.id, entity_type=EntityType.material_need,
                 entity_id=need_id, action=AuditAction.delete, changes={"material": [name, None]})
    db_session.commit()
    return {"id": need_id}
