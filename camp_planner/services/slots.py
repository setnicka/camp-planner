"""Slot attendees and the batch timeline save.

All slot placement (add / move / remove) goes through save_timeline — one atomic batch
under the camp.timeline_rev optimistic lock (no single-slot endpoints; a slot's role is
fixed at creation). update_slot patches a slot's attendees and/or display-name override,
neither of which is placement. Slot datetimes are naive local values (see timeline.py);
the schemas enforce start<end.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from camp_planner.extensions import db_session
from camp_planner.models.audit import AuditAction, EntityType
from camp_planner.models.slot import Slot, SlotAssignment
from camp_planner.services import audit, errors, google_sync, orgs, serialize
from camp_planner.services.timeline import build_timeline, bump_timeline_rev, span_in_window

if TYPE_CHECKING:
    from camp_planner.models.camp import Camp
    from camp_planner.schemas import SlotUpdateIn, TimelineSaveIn


def update_slot(slot: Slot, payload: SlotUpdateIn) -> dict:
    """Patch a slot's attendees and/or its display-name override. Only the fields present
    in the request are touched (an empty override_name clears it). Neither is placement, so
    this does not bump timeline_rev; both feed the Google event (orgs → description, name →
    title), so any change re-pushes it."""
    camp = slot.activity.camp
    fields = payload.model_fields_set
    changes: dict = {}

    if "org_ids" in fields and payload.org_ids is not None:  # explicit null → unchanged; [] → clear
        changes |= orgs.replace_assignments(slot, camp, payload.org_ids, SlotAssignment)

    if "override_name" in fields:
        new_name = (payload.override_name or "").strip() or None
        if new_name != slot.override_name:
            changes["override_name"] = [slot.override_name, new_name]
            slot.override_name = new_name

    if changes:  # nothing changed → no write, no audit row, no Google re-push
        audit.record(camp_id=camp.id, activity_id=slot.activity_id, entity_type=EntityType.slot,
                     entity_id=slot.id, action=AuditAction.update, changes=changes)
        google_sync.enqueue_upsert(camp, slot)
        db_session.commit()
    return {"orgs": serialize.slot_orgs(slot), "override_name": slot.override_name}


def save_timeline(camp: Camp, payload: TimelineSaveIn) -> dict:
    """Apply one editing batch atomically (creates + moves + deletes) under the rev
    optimistic lock (force=True skips the check — the conflict dialog's deliberate
    overwrite). A stale rev raises Conflict carrying the fresh timeline to reconcile
    against. Returns the new rev and the created slots (in `creates` order, for id mapping)."""
    if not payload.force and payload.rev != camp.timeline_rev:
        raise errors.Conflict(
            "Časový plán mezitím někdo změnil. Načtěte ho prosím znovu.",
            rev=camp.timeline_rev, timeline=build_timeline(camp),
        )

    by_id = {s.id: s for activity in camp.activities for s in activity.slots}
    activity_ids = {activity.id for activity in camp.activities}

    def _slot(slot_id: int) -> Slot:
        slot = by_id.get(slot_id)
        if slot is None:
            raise errors.Invalid("Změny: blok nepatří této akci.")
        return slot

    def _check_window(start_at, end_at) -> None:
        # Outside the day windows a slot persists but slice_segments clamps it away — unreachable.
        if not span_in_window(camp, start_at, end_at):
            raise errors.Invalid("Změny: blok leží mimo dny akce.")

    def _record(slot: Slot, action: AuditAction, changes: dict) -> None:
        # Per-slot rows sit under the slot's activity, so its history shows which of its
        # slots were added, moved or removed.
        audit.record(camp_id=camp.id, activity_id=slot.activity_id, entity_type=EntityType.slot,
                     entity_id=slot.id, action=action, changes=changes)

    # The batch summary goes first: the feed orders by id.
    retyped = sum(_slot(r.slot_id).role != r.role for r in payload.retypes)
    audit.record(camp_id=camp.id, entity_type=EntityType.timeline, entity_id=None, action=AuditAction.update,
                 changes={"moved": len(payload.moves), "created": len(payload.creates),
                          "retyped": retyped, "deleted": len(payload.deletes)})

    # Each change is mirrored to Google as it is made (a no-op unless the camp is
    # connected); drain delivers it out of band.
    created: list[Slot] = []
    for spec in payload.creates:
        if spec.activity_id not in activity_ids:
            raise errors.Invalid("Změny: aktivita nepatří této akci.")
        _check_window(spec.start_at, spec.end_at)
        slot = Slot(activity_id=spec.activity_id, role=spec.role,
                    start_at=spec.start_at, end_at=spec.end_at)
        db_session.add(slot)
        created.append(slot)
    db_session.flush()  # ids for the created slots' audit rows, which keep their place in the feed
    for slot in created:
        _record(slot, AuditAction.create, {"role": [None, slot.role], "start_at": [None, slot.start_at],
                                           "end_at": [None, slot.end_at]})
        google_sync.enqueue_upsert(camp, slot)

    for move in payload.moves:
        slot = _slot(move.slot_id)
        _check_window(move.start_at, move.end_at)
        # only the edge(s) that moved; a no-op move writes nothing
        if changes := audit.apply_changes(slot, {"start_at": move.start_at, "end_at": move.end_at}):
            _record(slot, AuditAction.update, changes)
            google_sync.enqueue_upsert(camp, slot)

    for retype in payload.retypes:
        slot = _slot(retype.slot_id)
        if changes := audit.apply_changes(slot, {"role": retype.role}):
            _record(slot, AuditAction.update, changes)
            google_sync.enqueue_upsert(camp, slot)

    for slot_id in payload.deletes:
        slot = _slot(slot_id)
        _record(slot, AuditAction.delete,
                {"start_at": [slot.start_at, None], "end_at": [slot.end_at, None]})
        google_sync.enqueue_delete(camp, slot.google_event_id)
        db_session.delete(slot)

    bump_timeline_rev(camp)
    db_session.commit()
    return {"rev": camp.timeline_rev, "created": [serialize.slot(s) for s in created]}
