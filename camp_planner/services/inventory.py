"""The warehouse (sklad): boxes, items, photos and inventory checks.

Global, so nothing here takes a camp and every audit row carries camp_id=None.

A check collects observations (InventoryCheckRecord) while people walk the boxes;
completing it writes them into the items in one transaction (what a record means:
InventoryCheckRecord). The rules:

  * no record = nobody looked, completion leaves the item alone;
  * an observation wins over the master row, so finding a discarded thing revives it.
    The other way round, editing an item's box or discarded state while a check runs
    drops that item's observation: applying a stale one later would undo the edit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import g
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from camp_planner.extensions import db, db_session
from camp_planner.models.audit import AuditAction, EntityType
from camp_planner.models.common import by_name, czech_sort_key, naive_utcnow as _now
from camp_planner.models.inventory import (
    InventoryBox,
    InventoryCheck,
    InventoryCheckRecord,
    InventoryCheckStatus,
    InventoryItem,
    InventoryPhoto,
)
from camp_planner.services import audit, errors, loaders, media, serialize

if TYPE_CHECKING:
    from collections.abc import Iterable

    from werkzeug.datastructures import FileStorage

    from camp_planner.schemas import (
        InventoryBoxCreate,
        InventoryBoxUpdateIn,
        InventoryCheckCreate,
        InventoryItemCreate,
        InventoryItemRestoreIn,
        InventoryItemUpdateIn,
        InventoryRecordIn,
    )


def _audit(entity_type: EntityType, entity_id: int | None, action: AuditAction,
           changes: dict | None = None) -> None:
    audit.record(camp_id=None, entity_type=entity_type, entity_id=entity_id,
                 action=action, changes=changes)


# --- reads -------------------------------------------------------------------

def _author() -> str:
    return g.identity.author


def _sorted_boxes(*options) -> list[InventoryBox]:
    """All boxes in Czech order. Callers about to walk box.items pass an eager-load
    option, so the walk is not one query per box."""
    boxes = db_session.scalars(db.select(InventoryBox).options(*options)).all()
    return by_name(boxes)


def _check_out(check: InventoryCheck | None) -> dict | None:
    return serialize.inventory_check(check) if check is not None else None


def _box_name(row) -> str | None:
    """A box name for an audit entry: the item's or the record's, None when it has none
    (a retired thing, or a box deleted after the observation)."""
    return row.box.name if row.box else None


def active_check() -> InventoryCheck | None:
    """The check being walked right now, if any. At most one exists (uq_inventory_check_active)."""
    return db_session.scalars(
        db.select(InventoryCheck).where(InventoryCheck.active_lock.is_not(None))
    ).first()


def _require_active_check() -> InventoryCheck:
    check = active_check()
    if check is None:
        raise errors.Invalid("Neprobíhá žádná inventura.")
    return check


def _records(check: InventoryCheck | None, *where,
             options=(), lock: bool = False) -> dict[int, InventoryCheckRecord]:
    """{item_id: record} for one check, narrowed by `where`. The warehouse is small
    enough to hold at once. record.item resolves from the identity map when the caller
    has loaded the items.

    `lock` reads under a lock, after the caller's claim: MySQL's REPEATABLE READ would
    otherwise answer from the snapshot of the request's first query and miss a record
    committed by whoever held the claim before us."""
    if check is None:
        return {}
    query = (db.select(InventoryCheckRecord)
             .where(InventoryCheckRecord.check_id == check.id, *where)
             .options(*options))
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return {r.item_id: r for r in db_session.scalars(query).all()}


def _box_records(check: InventoryCheck | None, box: InventoryBox) -> dict[int, InventoryCheckRecord]:
    """What one box page needs: observations about the things filed here, plus the ones
    pointing at this box from elsewhere (a move in). Both legs are indexed."""
    return _records(
        check,
        db.or_(InventoryCheckRecord.box_id == box.id,
               InventoryCheckRecord.item_id.in_([i.id for i in box.items])))


def _any_record(*where) -> bool:
    """Whether any observation matches. Joins the checks, so a caller can ask about
    finished ones."""
    return db_session.scalar(
        db.select(InventoryCheckRecord.item_id)
        .join(InventoryCheck)
        .where(*where)
        .limit(1)) is not None


# Finished checks; the query must join InventoryCheck.
_COMPLETED = InventoryCheck.active_lock.is_(None)


def _by_box(records: Iterable[InventoryCheckRecord]) -> dict[int | None, list[InventoryCheckRecord]]:
    """Observations grouped by the box they point at, so a page listing every box walks
    the records once instead of once per box."""
    out: dict[int | None, list[InventoryCheckRecord]] = {}
    for record in records:
        out.setdefault(record.box_id, []).append(record)
    return out


def _listed_ids(box: InventoryBox,
                by_box: dict[int | None, list[InventoryCheckRecord]]) -> set[int]:
    """The ids a box page lists: what is filed here, plus what the check has moved in
    here (including revived items, whose master row still has no box)."""
    return ({i.id for i in box.items if i.discarded_at is None}
            | {r.item_id for r in by_box.get(box.id, ())})


def _box_items(box: InventoryBox,
               by_box: dict[int | None, list[InventoryCheckRecord]]) -> dict[int, InventoryItem]:
    """What a box page lists, by id."""
    filed = {i.id: i for i in box.items}
    moved_in = {r.item_id: r for r in by_box.get(box.id, ())}
    return {item_id: filed[item_id] if item_id in filed else moved_in[item_id].item
            for item_id in _listed_ids(box, by_box)}


def _box_progress(
    box: InventoryBox, records: dict[int, InventoryCheckRecord],
    by_box: dict[int | None, list[InventoryCheckRecord]],
) -> tuple[dict[int, InventoryItem], int, int]:
    """A box's listing plus its check progress (checked, total). Its work is what the
    check says is here: a thing moved out stops counting, a thing moved in (or revived
    here) starts."""
    items = _box_items(box, by_box)
    mine = [i for i in items.values()
            if (records[i.id].box_id if i.id in records else i.box_id) == box.id]
    return items, sum(1 for i in mine if i.id in records), len(mine)


def _box_state(box: InventoryBox, check: InventoryCheck | None) -> dict:
    """One box page's whole state, the running check included."""
    records = _box_records(check, box)
    # The caller loaded box.items with their photos; only things observed here from
    # elsewhere are missing, and record.item resolves from the identity map once fetched.
    missing = set(records) - {i.id for i in box.items}
    if missing:
        db_session.scalars(db.select(InventoryItem).where(InventoryItem.id.in_(missing))
                           .options(*loaders.INVENTORY_ITEMS)).all()
    items, checked, total = _box_progress(box, records, _by_box(records.values()))
    listed = by_name(items.values())
    return serialize.inventory_box_state(
        box, listed,
        [records[i.id] for i in listed if i.id in records],
        checked=checked, total=total, check=check,
    )


def box_state(box: InventoryBox) -> dict:
    """GET …/state: the box page's refresh."""
    return {"state": _box_state(box, active_check())}


def overview_data() -> dict:
    """The warehouse overview: every box with its contents, the discarded shelf, the
    running check's progress and the finished checks."""
    check = active_check()
    # No item leg: the boxes and the shelf below load every observed thing.
    records = _records(check)
    by_box = _by_box(records.values())
    # Before the boxes, so a retired thing found by the check is loaded when a box lists it.
    discarded = by_name(db_session.scalars(
        db.select(InventoryItem)
        .where(InventoryItem.discarded_at.is_not(None))
        .options(*loaders.INVENTORY_ITEMS)
    ).all())
    boxes = []
    for box in _sorted_boxes(*loaders.INVENTORY_BOX):
        items, checked, total = _box_progress(box, records, by_box)
        boxes.append({
            "box": serialize.inventory_box(box),
            "items": [serialize.inventory_item(i) for i in by_name(items.values())],
            "checked": checked,
            "total": total,
        })
    # Found retired things by target box: the shelf shows where each is heading instead
    # of offering a second revive.
    revived = {}
    for item in discarded:
        record = records.get(item.id)
        if record is not None and not record.discarded and record.box_id is not None:
            revived[item.id] = record.box_id
    return {
        "boxes": boxes,
        "discarded": [serialize.inventory_item(i) for i in discarded],
        "revived": revived,
        "active_check": _check_out(check),
    }


def box_data(box: InventoryBox) -> dict:
    """One box page's data: its state, the boxes things can move to and every item in the
    warehouse, discarded included. Past checks load on demand through box_history."""
    all_items = by_name(db_session.scalars(db.select(InventoryItem)).all())
    check = active_check()
    # Deleting a box referenced by finished checks degrades their history to "(smazaná
    # krabice)"; the delete confirm warns about it from this flag.
    in_history = _any_record(
        _COMPLETED,
        db.or_(InventoryCheckRecord.box_id == box.id,
               InventoryCheckRecord.from_box_id == box.id))
    state = _box_state(box, check)
    # Whether the "Historie" button has anything to show: a finished check that said
    # something about a thing listed here (the same filter box_history applies).
    item_ids = [i["id"] for i in state["items"]]
    has_history = bool(item_ids) and _any_record(
        _COMPLETED, InventoryCheckRecord.item_id.in_(item_ids))
    return {
        "state": state,
        "boxes": [serialize.inventory_box(b) for b in _sorted_boxes()],
        "all_items": [serialize.inventory_item_ref(i) for i in all_items],
        "in_history": in_history,
        "has_history": has_history,
        # Why Delete is unavailable, straight from the predicate delete_box refuses on.
        "delete_blocked": _delete_blocker(box, check),
    }


def box_history(box: InventoryBox) -> dict:
    """What the finished checks said about the things this box now lists, oldest check
    first. Backs the box page's on-demand "načíst inventury" matrix."""
    item_ids = _listed_ids(box, _by_box(_box_records(active_check(), box).values()))
    history = db_session.scalars(
        db.select(InventoryCheckRecord)
        .join(InventoryCheck)
        .where(_COMPLETED, InventoryCheckRecord.item_id.in_(item_ids))
    ).all() if item_ids else []
    return {
        "checks": [serialize.inventory_check(c) for c in reversed(_completed_checks())],
        "records": [serialize.inventory_record(r) for r in history],
    }


def _completed_checks() -> list[InventoryCheck]:
    return db_session.scalars(
        db.select(InventoryCheck).where(_COMPLETED).order_by(InventoryCheck.completed_at.desc())
    ).all()


def _progress(records: dict[int, InventoryCheckRecord]) -> list[dict]:
    """Per-box (checked, total) of the running check."""
    by_box = _by_box(records.values())
    out = []
    for box in _sorted_boxes(*loaders.INVENTORY_BOX_ITEMS):
        _, checked, total = _box_progress(box, records, by_box)
        out.append({"box": serialize.inventory_box(box), "checked": checked, "total": total})
    return out


def checks_data() -> dict:
    """The checks page: the running check with its per-box progress, and the finished ones."""
    check = active_check()
    # No eager legs: _progress loads every box's items.
    records = _records(check)
    return {
        "active_check": _check_out(check),
        "progress": _progress(records) if check is not None else [],
        "checks": [serialize.inventory_check(c) for c in _completed_checks()],
    }


def check_data(check: InventoryCheck) -> dict:
    """A finished check, read-only: its observations grouped by the box they were made in.
    Rows whose box has since been deleted land in a nameless group at the end."""
    groups = _by_box(check.records)
    boxes = {b.id: b for b in _sorted_boxes()}
    ordered = [b for b in boxes if b in groups] + ([None] if None in groups else [])
    out = [{"box": serialize.inventory_box(boxes[box_id]) if box_id is not None else None,
            "records": [serialize.inventory_record(r) for r in
                        sorted(groups[box_id], key=lambda r: czech_sort_key(r.item.name))],
            "names": {r.item_id: r.item.name for r in groups[box_id]}}
           for box_id in ordered]
    # All boxes ride along so move tooltips can name a source box that has no group here.
    return {"check": serialize.inventory_check(check), "groups": out,
            "boxes": [serialize.inventory_box(b) for b in boxes.values()]}


# --- boxes -------------------------------------------------------------------

def _clean_name(name: str) -> str:
    """Names come stripped; " " would pass pydantic's min_length and store as empty."""
    name = name.strip()
    if not name:
        raise errors.Invalid("Název nesmí být prázdný.")
    return name


def create_box(payload: InventoryBoxCreate) -> dict:
    name = _clean_name(payload.name)
    box = InventoryBox(name=name, location=payload.location, note=payload.note,
                       virtual=payload.virtual)
    db_session.add(box)
    try:
        db_session.flush()
    except IntegrityError:
        db_session.rollback()
        raise errors.Invalid(f"Krabice „{name}“ už existuje.") from None
    _audit(EntityType.inventory_box, box.id, AuditAction.create, {"name": [None, name]})
    db_session.commit()
    return {"box": serialize.inventory_box(box)}


def update_box(box: InventoryBox, payload: InventoryBoxUpdateIn) -> dict:
    if payload.name is not None:
        payload.name = _clean_name(payload.name)
    changes = audit.apply_patch(box, payload, ("name", "location", "note", "virtual"))
    if changes:
        try:
            db_session.flush()
        except IntegrityError:
            db_session.rollback()
            raise errors.Invalid("Krabice s tímto názvem už existuje.") from None
        _audit(EntityType.inventory_box, box.id, AuditAction.update, changes)
        db_session.commit()
    return {"box": serialize.inventory_box(box)}


def _delete_blocker(box: InventoryBox, check: InventoryCheck | None) -> str | None:
    """Why this box cannot be deleted, or None. The box page greys its Delete button with
    this text, so the affordance cannot drift from the refusal."""
    if box.items:
        return (f"Krabici „{box.name}“ nelze smazat – jsou v ní věci. "
                f"Je potřeba je nejdřív přesunout jinam nebo vyřadit.")
    if check is not None and _any_record(InventoryCheckRecord.check_id == check.id,
                                         InventoryCheckRecord.box_id == box.id):
        return f"Krabici „{box.name}“ nelze smazat – probíhající inventura do ní něco umístila."
    return None


def delete_box(box: InventoryBox) -> dict:
    """Delete an empty box. Refused while it holds anything, or while the running check
    has put something in it; observations in finished checks only lose their box (SET NULL)."""
    blocker = _delete_blocker(box, active_check())
    if blocker is not None:
        raise errors.Invalid(blocker)
    box_id, name = box.id, box.name
    db_session.delete(box)
    _audit(EntityType.inventory_box, box_id, AuditAction.delete, {"name": [name, None]})
    db_session.commit()
    return {"id": box_id}


# --- items -------------------------------------------------------------------

def _require_box(box_id: int) -> InventoryBox:
    box = db_session.get(InventoryBox, box_id)
    if box is None:
        raise errors.Invalid("Neznámá krabice.")
    return box


def create_item(payload: InventoryItemCreate) -> dict:
    """Create an item in a box.

    During a check it is also recorded as observed. The count goes on the item, so a
    cancel keeps it.
    """
    box = _require_box(payload.box_id)
    check = active_check()
    if check is not None:
        _claim_check_active(check)
    item = InventoryItem(
        name=_clean_name(payload.name), box_id=box.id, alt_names=payload.alt_names,
        url=payload.url, note=payload.note, unit=payload.unit, count=payload.count,
    )
    db_session.add(item)
    db_session.flush()
    _audit(EntityType.inventory_item, item.id, AuditAction.create,
           {"name": [None, item.name], "box": [None, box.name]})
    if check is not None:
        db_session.add(_new_record(check, item))
    db_session.commit()
    return {"item": serialize.inventory_item(item)}


def update_item(item: InventoryItem, payload: InventoryItemUpdateIn) -> dict:
    """Edit an item's fields. A new box_id is an ordinary move; the amount is refused
    while a check runs, because that is the check's to write."""
    check = active_check()
    move_to = None
    if "box_id" in payload.model_fields_set:
        if payload.box_id is None:
            raise errors.Invalid("Věc musí být v krabici.")
        box = _require_box(payload.box_id)
        if item.box_id != box.id:
            if item.discarded_at is not None:
                raise errors.Invalid("Vyřazenou věc lze umístit jen přes „Vrátit do skladu“.")
            move_to = box
    # Refused, not resolved: the observation would revert the edit at completion, or the
    # edit would drop somebody's count.
    if check is not None and any(
            field in payload.model_fields_set and getattr(payload, field) != getattr(item, field)
            for field in ("count", "unit")):
        raise errors.Invalid("Počet a jednotku během inventury zapisuje inventura "
                             "(tlačítko „jiný počet“ na řádku).")
    if payload.name is not None:
        payload.name = _clean_name(payload.name)
    changes = audit.apply_patch(item, payload, ("name", "alt_names", "url", "note", "unit", "count"))
    if move_to is not None:
        changes["box"] = [_box_name(item), move_to.name]
        item.box_id = move_to.id
        _drop_observation(item, check)
    if changes:
        _audit(EntityType.inventory_item, item.id, AuditAction.update, changes)
        db_session.commit()
    return {"item": serialize.inventory_item(item)}


def discard_item(item: InventoryItem) -> dict:
    """Retire an item: it keeps its history but leaves the boxes. Reversible via restore."""
    if item.discarded_at is not None:
        raise errors.Invalid("Věc je už vyřazená.")
    now = _now()
    changes = {"discarded_at": [None, now], "box": [_box_name(item), None]}
    item.discarded_at = now
    item.box_id = None
    _drop_observation(item, active_check())
    _audit(EntityType.inventory_item, item.id, AuditAction.update, changes)
    db_session.commit()
    return {"item": serialize.inventory_item(item)}


def restore_item(item: InventoryItem, payload: InventoryItemRestoreIn) -> dict:
    if item.discarded_at is None:
        raise errors.Invalid("Věc není vyřazená.")
    box = _require_box(payload.box_id)
    changes = {"discarded_at": [item.discarded_at, None], "box": [None, box.name]}
    item.discarded_at = None
    item.box_id = box.id
    _drop_observation(item, active_check())
    _audit(EntityType.inventory_item, item.id, AuditAction.update, changes)
    db_session.commit()
    return {"item": serialize.inventory_item(item)}


def delete_item(item: InventoryItem) -> dict:
    """Erase an item for good, with its photos and its whole check history. For mistakes
    only; "we don't have it anymore" is discard_item."""
    item_id, name = item.id, item.name
    filenames = [p.filename for p in item.photos]
    db_session.delete(item)
    _audit(EntityType.inventory_item, item_id, AuditAction.delete, {"name": [name, None]})
    db_session.commit()
    media.delete(filenames)
    return {"id": item_id}


def _find_record(check: InventoryCheck, item: InventoryItem) -> InventoryCheckRecord | None:
    return _records(check, InventoryCheckRecord.item_id == item.id, lock=True).get(item.id)


def _drop_observation(item: InventoryItem, check: InventoryCheck | None) -> None:
    """Forget the running check's observation of this item, if it has one."""
    if check is None:
        return
    _claim_check_active(check)
    record = _find_record(check, item)
    if record is not None:
        db_session.delete(record)


# --- photos ------------------------------------------------------------------

def add_photos(item: InventoryItem, uploads: list[FileStorage]) -> dict:
    """Store uploaded images and attach them, appending after the existing ones."""
    if not uploads:
        raise errors.Invalid("Žádná fotka k nahrání.")
    before = len(item.photos)
    next_order = max((p.sort_order for p in item.photos), default=-1) + 1
    stored: list[str] = []
    try:
        for offset, upload in enumerate(uploads):
            filename = media.store(upload.stream)
            stored.append(filename)
            item.photos.append(
                InventoryPhoto(filename=filename, sort_order=next_order + offset))
        _audit(EntityType.inventory_item, item.id, AuditAction.update,
               {"photos": [before, len(item.photos)]})
        db_session.commit()
    except Exception:
        # Nothing committed, so no row names the files; the staged ones leave with the
        # request's session.
        media.delete(stored)
        raise
    return {"item": serialize.inventory_item(item)}


def delete_photo(photo: InventoryPhoto) -> dict:
    item, filename = photo.item, photo.filename
    before = len(item.photos)
    item.photos.remove(photo)      # delete-orphan cascade removes the row
    _audit(EntityType.inventory_item, item.id, AuditAction.update,
           {"photos": [before, before - 1]})
    db_session.commit()
    media.delete([filename])
    return {"item": serialize.inventory_item(item)}


def set_title_photo(photo: InventoryPhoto) -> dict:
    """Make this the item's title photo by pulling it to the front; the rest keep their
    relative order."""
    item = photo.item
    others = [p for p in item.photos if p.id != photo.id]
    was = item.photos[0].id
    for order, current in enumerate([photo, *others]):
        current.sort_order = order
    if photo.id != was:
        _audit(EntityType.inventory_item, item.id, AuditAction.update,
               {"title_photo": [was, photo.id]})
    db_session.commit()
    return {"item": serialize.inventory_item(item)}


# --- check lifecycle ---------------------------------------------------------

def start_check(payload: InventoryCheckCreate) -> dict:
    """Open a check. The DB refuses a second active one (uq_inventory_check_active), so two
    simultaneous clicks cannot both succeed."""
    check = InventoryCheck(name=_clean_name(payload.name), author=_author())
    db_session.add(check)
    try:
        db_session.flush()
    except IntegrityError:
        db_session.rollback()
        raise errors.Invalid("Jedna inventura už probíhá.") from None
    _audit(EntityType.inventory_check, check.id, AuditAction.create,
           {"name": [None, check.name]})
    db_session.commit()
    return {"check": serialize.inventory_check(check)}


def _claim(check: InventoryCheck, **values) -> bool:
    """Take the check's row lock inside this transaction, but only while it is still
    active: everything that must not land on a frozen check goes through this one
    conditional UPDATE, and racing writers serialize on it."""
    claimed = db_session.execute(
        update(InventoryCheck)
        .where(InventoryCheck.id == check.id, InventoryCheck.active_lock.is_not(None))
        .values(**values)
    )
    return claimed.rowcount == 1


def cancel_check(check: InventoryCheck) -> dict:
    """Throw away an unfinished check with all its observations; nothing propagates."""
    if check.status is not InventoryCheckStatus.active:
        raise errors.Invalid("Dokončenou inventuru nelze zrušit.")
    check_id, name = check.id, check.name
    # Claimed the way complete_check claims it: a cancel racing a completion must not
    # delete the finished check and every observation it just applied.
    _claim_check_active(check)
    db_session.delete(check)
    _audit(EntityType.inventory_check, check_id, AuditAction.delete, {"name": [name, None]})
    db_session.commit()
    return {"id": check_id}


def _plan(record: InventoryCheckRecord) -> dict:
    """The item columns completing this observation writes. A record is a full snapshot,
    so the values come from it alone; a thing already retired only keeps its original
    retirement date."""
    item = record.item
    if record.discarded:
        return ({"box_id": None} if item.discarded_at is not None
                else {"discarded_at": _now(), "box_id": None})
    plan: dict = {"count": record.count, "unit": record.unit}
    # A record whose box was deleted mid-check (box_id SET NULL) carries no position any
    # more, so it must neither move the thing nor revive it into no box at all.
    if record.box_id is not None:
        plan["discarded_at"] = None
        plan["box_id"] = record.box_id
    return plan


def _pending(record: InventoryCheckRecord) -> dict[str, list]:
    """What _plan would change about the item, as an audit diff, without applying it."""
    item = record.item
    return {field: [getattr(item, field), value]
            for field, value in _plan(record).items() if getattr(item, field) != value}


def _counters(record: InventoryCheckRecord, changes: dict[str, list]) -> set[str]:
    """Which summary counters a completed observation bumps, read off what it changes."""
    if record.discarded:
        return {"discarded"} if "discarded_at" in changes else set()
    bumped = set()
    if "discarded_at" in changes:
        bumped.add("revived")
    # A revive also lands in a box, but came from none: that is not a move.
    if "box_id" in changes and record.from_box_id is not None:
        bumped.add("moved")
    if changes.keys() & {"count", "unit"}:
        bumped.add("adjusted")
    return bumped


def _tally(counters: list[set[str]]) -> dict:
    summary = {"checked": len(counters), "adjusted": 0, "discarded": 0, "moved": 0, "revived": 0}
    for bumped in counters:
        for key in bumped:
            summary[key] += 1
    return summary


def preview_check(check: InventoryCheck) -> dict:
    """What completing the check right now would do, for the confirm dialog. Reads only."""
    if check.status is not InventoryCheckStatus.active:
        raise errors.Invalid("Inventura je už dokončená.")
    records = _records(check)
    # Progress first: it loads the items record.item needs (only retired ones load lazily).
    progress = _progress(records)
    return {"summary": _tally([_counters(r, _pending(r)) for r in records.values()]),
            "progress": progress}


def complete_check(check: InventoryCheck) -> dict:
    """Write every observation into its item, in one transaction, and freeze the check.

    Claimed first (_claim), so a double click cannot apply the observations twice.
    """
    if not _claim(check, active_lock=None, completed_at=_now()):
        raise errors.Conflict("Inventura je už dokončená.")

    counters = []
    for record in _records(check, options=loaders.INVENTORY_COMPLETION, lock=True).values():
        item = record.item
        was_in = _box_name(item)
        changes = audit.apply_changes(item, _plan(record))
        counters.append(_counters(record, changes))
        # The audit log names boxes instead of pointing at ids: it is the only place a
        # deleted box's name survives. The new one is read off the record, because
        # item.box is the relationship as loaded, still the old box.
        if "box_id" in changes:
            del changes["box_id"]
            changes["box"] = [was_in, None if item.box_id is None else record.box.name]
        if record.note:
            line = f"{check.name}: {record.note}"
            changes["note"] = [item.note, f"{item.note}\n{line}" if item.note else line]
            item.note = changes["note"][1]
        if changes:
            _audit(EntityType.inventory_item, item.id, AuditAction.update, changes)

    summary = _tally(counters)
    check.summary = summary
    _audit(EntityType.inventory_check, check.id, AuditAction.update,
           {"status": ["active", "completed"], "summary": [None, summary]})
    db_session.commit()
    return {"check": serialize.inventory_check(check)}


# --- observations ------------------------------------------------------------

def _new_record(check: InventoryCheck, item: InventoryItem) -> InventoryCheckRecord:
    """A fresh observation, seeded from the item: "it is where and how we have it filed"."""
    return InventoryCheckRecord(
        check_id=check.id, item_id=item.id, discarded=False,
        count=item.count, unit=item.unit, box_id=item.box_id,
        from_box_id=item.box_id, author=_author(),
    )


def _get_or_create_record(check: InventoryCheck, item: InventoryItem) -> InventoryCheckRecord:
    """The item's observation in this check, created (seeded) if it has none yet. The
    caller's claim serializes writers, so two people saving one item meet here in turn
    and the second finds the first one's row."""
    record = _find_record(check, item)
    if record is None:
        record = _new_record(check, item)
        db_session.add(record)
    return record


def _claim_check_active(check: InventoryCheck) -> None:
    """Guard a write to the check's records against a concurrent completion or cancel.
    First, before any record is read or written: the lock then orders every writer the
    same way (a record insert only share-locks the check row, and upgrading that to the
    claim's exclusive lock deadlocks two writers on MySQL)."""
    if not _claim(check, active_lock=1):   # no-op value, the lock is the point
        raise errors.Conflict("Inventura už byla mezitím dokončena nebo zrušena.")


def upsert_record(box: InventoryBox, item: InventoryItem, payload: InventoryRecordIn) -> dict:
    """Record what somebody saw of one item while working in `box`; answers with the
    whole state of `box` (see InventoryBoxStateOut)."""
    check = _require_active_check()
    sent = payload.model_fields_set
    if payload.discarded and (payload.count is not None or payload.unit is not None):
        raise errors.Invalid("Vyřazená věc nemůže mít zadaný počet.")
    if "box_id" in sent:
        if payload.box_id is None:
            raise errors.Invalid("Nalezená věc musí mít krabici.")
        _require_box(payload.box_id)
    _claim_check_active(check)
    record = _get_or_create_record(check, item)

    if payload.box_id is not None:
        record.box_id = payload.box_id
    if payload.discarded is not None:
        # Coming back from discarded means somebody found it: re-seed, they will overwrite
        # what they actually counted in the same request if they counted.
        if record.discarded and not payload.discarded:
            record.count, record.unit = item.count, item.unit
        record.discarded = payload.discarded
    if "count" in sent:
        record.count = payload.count
    if "unit" in sent:
        record.unit = payload.unit
    if "note" in sent:
        record.note = payload.note
    if record.discarded:
        record.count, record.unit = None, None
    elif record.box_id is None:
        raise errors.Invalid("Nalezená věc musí mít krabici.")

    record.author = _author()
    # Set by hand, not left to onupdate: a byte-identical re-save would not dirty the
    # row, and confirming the state must still bump the timestamp.
    record.updated_at = _now()
    # Built before the commit, which would expire everything this request has loaded
    # and make the answer re-read the box, its items and their photos. Autoflush has
    # already made the record itself visible to those queries.
    state = _box_state(box, check)
    db_session.commit()
    return {"state": state}


def delete_record(box: InventoryBox, item: InventoryItem) -> dict:
    """Forget the observation, returning the item to unchecked."""
    check = _require_active_check()
    _drop_observation(item, check)
    state = _box_state(box, check)   # before the commit expires it all, see upsert_record
    db_session.commit()
    return {"state": state}
