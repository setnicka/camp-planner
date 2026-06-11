"""Slot attendees and the batch timeline save.

All slot placement (add / move / remove) goes through save_timeline — one atomic batch
under the camp.timeline_rev optimistic lock (no single-slot endpoints; a slot's role is
fixed at creation). set_slot_orgs manages attendees, which aren't placement. Slot
datetimes are naive local values (see timeline.py); the schemas enforce start<end.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from camp_planner.extensions import db
from camp_planner.models.audit import AuditAction, EntityType
from camp_planner.models.common import czech_sort_key
from camp_planner.models.slot import Slot, SlotAssignment
from camp_planner.services import audit, errors, serialize
from camp_planner.services.timeline import build_timeline, bump_timeline_rev

if TYPE_CHECKING:
    from camp_planner.models.camp import Camp
    from camp_planner.schemas import SlotOrgsIn, TimelineSaveIn


def set_slot_orgs(slot: Slot, payload: SlotOrgsIn) -> dict:
    """Replace the set of orgs attending this slot. Attendees aren't placement, so this
    does not bump timeline_rev."""
    camp = slot.activity.camp
    initials = {o.id: o.initials for o in camp.orgs}
    for org_id in payload.org_ids:  # schema already rejected duplicate ids
        if org_id not in initials:
            raise errors.Invalid("Orgové: neznámý org této akce.")

    current = {a.org_id for a in slot.assignments}
    if current == set(payload.org_ids):
        return {"orgs": serialize.slot_orgs(slot)}  # unchanged → no write, no audit row

    by_czech = lambda ids: sorted((initials[i] for i in ids), key=czech_sort_key)  # noqa: E731
    before = by_czech(current)
    slot.assignments = [SlotAssignment(org_id=i) for i in payload.org_ids]  # delete-orphan drops the old rows
    audit.record(camp_id=camp.id, activity_id=slot.activity_id, entity_type=EntityType.slot,
                 entity_id=slot.id, action=AuditAction.update,
                 changes={"orgs": [before, by_czech(payload.org_ids)]})
    db.session.commit()
    return {"orgs": serialize.slot_orgs(slot)}


def save_timeline(camp: Camp, payload: TimelineSaveIn) -> dict:
    """Apply one editing batch atomically (creates + moves + deletes) under the rev
    optimistic lock. A stale rev raises Conflict carrying the fresh timeline to reconcile
    against. Returns the new rev and the created slots (in `creates` order, for id mapping)."""
    if payload.rev is not None and payload.rev != camp.timeline_rev:
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

    created: list[Slot] = []
    for spec in payload.creates:
        if spec.activity_id not in activity_ids:
            raise errors.Invalid("Změny: aktivita nepatří této akci.")
        slot = Slot(activity_id=spec.activity_id, role=spec.role,
                    start_at=spec.start_at, end_at=spec.end_at)
        db.session.add(slot)
        created.append(slot)

    for move in payload.moves:
        slot = _slot(move.slot_id)
        slot.start_at, slot.end_at = move.start_at, move.end_at

    for slot_id in payload.deletes:
        db.session.delete(_slot(slot_id))

    bump_timeline_rev(camp)
    db.session.flush()  # assign ids to the created slots before serializing
    audit.record(camp_id=camp.id, entity_type=EntityType.timeline, entity_id=None, action=AuditAction.update,
                 message=f"{len(payload.moves)} přesunuto, {len(payload.creates)} přidáno, "
                         f"{len(payload.deletes)} smazáno")
    db.session.commit()
    return {"rev": camp.timeline_rev, "created": [serialize.slot(s) for s in created]}
