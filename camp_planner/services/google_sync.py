"""Outbound Google Calendar sync: stage changes, deliver them out of band.

Write paths (save_timeline, activity update/delete) call enqueue_* to stage a
GoogleSyncOp on the session — no Google call, no commit — exactly like audit.record;
their own commit persists it. `drain` later delivers the queued ops to Google, so a slow
or unreachable Google never blocks nor fails a timeline edit. drain runs from the
`flask sync-google` cron command and the "Synchronizovat nyní" button.

Connecting/disconnecting a camp and the inbound (Google→Planner) reviewed import live
elsewhere (services/camps.py for the connection; inbound is a later phase).
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

from camp_planner.extensions import db
from camp_planner.models.activity import Activity, ActivityAssignment, OrgRole
from camp_planner.models.audit import AuditAction, EntityType
from camp_planner.models.common import czech_sort_key
from camp_planner.models.google import GoogleSyncOp, SyncOpKind
from camp_planner.models.slot import Slot, SlotAssignment, SlotRole
from camp_planner.services import audit, errors, google_client
from camp_planner.services.timeline import bump_timeline_rev

# An imported event longer than this is treated as a whole-camp span, not a slot, and skipped.
_MAX_IMPORT_HOURS = 48

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from camp_planner.models.camp import Camp
    from camp_planner.schemas import GooglePullDecisionIn


def _already_queued(camp: Camp, *conditions) -> bool:
    """Whether a matching outbound op is already pending. The query autoflushes the session,
    so it also sees ops staged earlier in the current transaction — hence repeated enqueues
    within one request collapse to a single row. (drain() still dedupes, to cover ops raced
    in by a concurrent request whose row this query couldn't yet see.)"""
    return db.session.scalar(
        db.select(GoogleSyncOp.id).where(GoogleSyncOp.camp_id == camp.id, *conditions).limit(1)
    ) is not None


def enqueue_upsert(camp: Camp, slot: Slot) -> None:
    """Stage a create-or-update of the slot's Google event. No-op if the camp isn't connected
    or an upsert for this slot is already queued (it rebuilds from the slot's current state,
    so one suffices). Call after the slot has an id (post-flush)."""
    if not camp.google_calendar_id:
        return
    if _already_queued(camp, GoogleSyncOp.slot_id == slot.id, GoogleSyncOp.op == SyncOpKind.upsert):
        return
    db.session.add(GoogleSyncOp(camp_id=camp.id, slot_id=slot.id, op=SyncOpKind.upsert))


def enqueue_delete(camp: Camp, google_event_id: str | None) -> None:
    """Stage deletion of an event whose slot is gone. No-op if the camp isn't connected, the
    slot was never synced (no event id), or a delete of this event is already queued."""
    if not camp.google_calendar_id or not google_event_id:
        return
    if _already_queued(camp, GoogleSyncOp.google_event_id == google_event_id,
                       GoogleSyncOp.op == SyncOpKind.delete):
        return
    db.session.add(
        GoogleSyncOp(camp_id=camp.id, op=SyncOpKind.delete, google_event_id=google_event_id)
    )


def pending_count(camp: Camp) -> int:
    """How many outbound ops are still queued for this camp (for the status UI)."""
    return db.session.scalar(
        db.select(db.func.count())
        .select_from(GoogleSyncOp)
        .where(GoogleSyncOp.camp_id == camp.id)
    )


def failure_summary(camp: Camp) -> tuple[int, str | None]:
    """(# of queued ops that have failed at least once, the most recent error text) — lets
    the UI surface a stuck sync, e.g. a calendar shared read-only so every push 403s."""
    failed = db.session.scalars(
        db.select(GoogleSyncOp)
        .where(GoogleSyncOp.camp_id == camp.id, GoogleSyncOp.attempts > 0)
        .order_by(GoogleSyncOp.id.desc())
    ).all()
    return len(failed), (failed[0].last_error if failed else None)


# Per-camp drain lock: (acquire SQL, release SQL, key-from-camp-id). Both statements take a single
# :k bind whose type differs by backend — Postgres advisory locks key on a bigint, while MySQL
# GET_LOCK needs a string name (an int errors on MySQL 8). Each key is namespaced so it can't
# collide with another feature's advisory lock: a "cp_drain_<id>" name on MySQL, and on Postgres
# our namespace in the high 32 bits with the camp id in the low 32. A backend without advisory
# locks (SQLite) maps to None — drain() then relies on write serialization + idempotent deletion.
_DRAIN_LOCK_NS = 0x6770  # "gp" — drain-lock namespace, kept clear of other advisory-lock users
_LOCK_SQL = {
    "postgresql": ("SELECT pg_try_advisory_lock(:k)", "SELECT pg_advisory_unlock(:k)",
                   lambda i: (_DRAIN_LOCK_NS << 32) | i),
    "mysql": ("SELECT GET_LOCK(:k, 0)", "SELECT RELEASE_LOCK(:k)", lambda i: f"cp_drain_{i}"),
}


@contextmanager
def _drain_lock(camp: Camp):
    """Non-blocking, per-camp cross-process mutex: the cron/sidecar and the "Synchronizovat nyní"
    button both call drain(), and without this they could double-insert events. Yields True to
    proceed, False to bow out (the holder delivers our ops too). The lock lives on a dedicated
    connection for the whole drain — so it outlives drain's mid-flow commit and a pooled-connection
    swap can't leak it — and is advisory, so it never blocks timeline edits. No-op on SQLite (no
    advisory locks), where the idempotent op-deletion in drain() covers the race."""
    sql = _LOCK_SQL.get(db.session.get_bind().dialect.name)
    if sql is None:
        yield True
        return
    acquire, release, make_key = sql
    key = make_key(camp.id)
    conn = db.engine.connect()
    try:
        got = conn.execute(db.text(acquire), {"k": key}).scalar()
        try:
            yield bool(got)
        finally:
            if got:
                conn.execute(db.text(release), {"k": key})
    finally:
        conn.close()


def drain(camp: Camp) -> dict:
    """Deliver all queued outbound ops for the camp to Google. A non-blocking per-camp lock (see
    _drain_lock) makes a second concurrent drain bow out, so the cron and the manual button can't
    double-push. Returns {pushed, failed, pending}."""
    if not camp.google_calendar_id:
        return {"pushed": 0, "failed": 0, "pending": 0}
    with _drain_lock(camp) as acquired:
        if not acquired:
            log.info("Google Calendar drain (camp %s): another drain holds the lock, skipping",
                     camp.slug)
            return {"pushed": 0, "failed": 0, "pending": pending_count(camp)}
        return _deliver_queued_ops(camp)


def _deliver_queued_ops(camp: Camp) -> dict:
    """Push each queued op to Google oldest-first, while holding the per-camp lock (see drain).
    Each op is removed on success; a failure bumps attempts / records the error and leaves the row
    for the next drain. Op removal is one idempotent bulk delete, so a drain that raced past the
    lock (only possible on SQLite) can't StaleDataError. Owns its transaction."""
    result = {"pushed": 0, "failed": 0, "pending": 0}
    cal = camp.google_calendar_id
    ops = db.session.scalars(
        db.select(GoogleSyncOp)
        .where(GoogleSyncOp.camp_id == camp.id)
        .order_by(GoogleSyncOp.id)
    ).all()

    # Collapse to one op per target: repeated upserts of a slot each rebuild the same event
    # from the slot's current state, and repeated deletes of an event are identical, so only
    # the newest is needed. Superseded ops are dropped here without any API call. (An upsert
    # whose slot was since deleted is handled in _push: the slot is gone → nothing to send.)
    upserts: dict[int, GoogleSyncOp] = {}
    deletes: dict[str, GoogleSyncOp] = {}
    done_ids: list[int] = []  # op rows to remove (superseded + delivered) — bulk-deleted below
    for op in ops:
        bucket, key = ((deletes, op.google_event_id) if op.op == SyncOpKind.delete
                       else (upserts, op.slot_id))
        if key in bucket:
            done_ids.append(bucket[key].id)  # older op for the same target → drop it
        bucket[key] = op

    def _push(op: GoogleSyncOp) -> None:
        if op.op == SyncOpKind.delete:
            if op.google_event_id:
                google_client.delete_event(cal, op.google_event_id)
                log.info("Google Calendar: deleted event %s (camp %s)", op.google_event_id, camp.slug)
            return
        slot = db.session.get(Slot, op.slot_id) if op.slot_id else None
        if slot is None:  # slot deleted before we pushed it — nothing to create
            return
        body = google_client.event_body(slot)  # includes colorId
        if slot.google_event_id:
            google_client.patch_event(cal, slot.google_event_id, body)
            log.info("Google Calendar: updated event %s for slot %s (camp %s)",
                     slot.google_event_id, slot.id, camp.slug)
        else:
            slot.google_event_id = google_client.insert_event(cal, body)
            log.info("Google Calendar: created event %s for slot %s (camp %s)",
                     slot.google_event_id, slot.id, camp.slug)

    for op in [*upserts.values(), *deletes.values()]:
        try:
            _push(op)
            done_ids.append(op.id)
            result["pushed"] += 1
        except Exception as exc:  # noqa: BLE001 — resilience: skip, retry on next drain
            op.attempts += 1
            op.last_error = str(exc)[:500]
            result["failed"] += 1
            log.warning("Google Calendar push failed (camp %s, %s, slot=%s, event=%s, attempt %d): %s",
                        camp.slug, op.op.value, op.slot_id, op.google_event_id, op.attempts, exc)

    if done_ids:  # one bulk delete (tolerates already-gone rows) rather than per-row ORM deletes
        db.session.execute(db.delete(GoogleSyncOp).where(GoogleSyncOp.id.in_(done_ids)))
    db.session.commit()
    result["pending"] = pending_count(camp)
    if ops:
        log.info("Google Calendar drain (camp %s): pushed=%d failed=%d pending=%d",
                 camp.slug, result["pushed"], result["failed"], result["pending"])
    return result


# --- inbound (Google → Planner), explicit & reviewed -------------------------

def camp_window(start_date, length_days: int, window_start_min: int) -> tuple[datetime, datetime]:
    """A camp's wall-clock span [start, end): from start_date at window_start_min, for
    length_days. Used to filter import candidates and to detect overlaps between camps that
    share one calendar. Takes raw fields so a *prospective* window can be checked too."""
    start = datetime.combine(start_date, time()) + timedelta(minutes=window_start_min)
    return start, start + timedelta(days=length_days)


# Initials tokens are separated by commas, semicolons, plus signs or whitespace; any
# parentheses are dropped (e.g. "(K) M+P, R" → K, M, P, R).
_TOKEN_SEP = re.compile(r"[,;+\s]+")
_GROUP_SEP = re.compile(r"[+\s]+")  # within one LOCATION comma-item (comma kept structural)


def _tokens(text: str | None, pattern: re.Pattern) -> list[str]:
    cleaned = (text or "").replace("(", " ").replace(")", " ")
    return [t for t in pattern.split(cleaned) if t]


def _match_initials(camp: Camp, text: str | None) -> tuple[list[int], list[str]]:
    """Parse a flat initials string into (matched org ids, unknown tokens), matching
    case-insensitively against the camp's orgs (first-seen order). Tokens are separated by
    commas, semicolons, plus signs or spaces, and parentheses are ignored."""
    by_initials = {o.initials.casefold(): o.id for o in camp.orgs}
    matched: list[int] = []
    unknown: list[str] = []
    for token in _tokens(text, _TOKEN_SEP):
        oid = by_initials.get(token.casefold())
        if oid is None:
            unknown.append(token)
        elif oid not in matched:
            matched.append(oid)
    return matched, unknown


def _initials(camp: Camp, org_ids: list[int], *, sort: bool = True) -> list[str]:
    """Initials for the given org ids. Czech-sorted by default; pass sort=False to keep the
    given id order (used where the first org is meaningful, e.g. the garant of a new import)."""
    by_id = {o.id: o.initials for o in camp.orgs}
    names = [by_id[i] for i in org_ids if i in by_id]
    return sorted(names, key=czech_sort_key) if sort else names


def _parse_location(camp: Camp, text: str | None) -> tuple[list[int], list[int], list[str]]:
    """Parse the LOCATION field into (garant ids, helper ids, unknown tokens). Comma is the
    main separator (and '(' / ')' count as commas too): the first comma item are the garants,
    each later item is a helper; within an item orgs are split on '+' or spaces. So
    "H (Á, B, L)" → garant H, helpers Á/B/L. Case-insensitive; each org counts once, in its
    first-seen role."""
    by_initials = {o.initials.casefold(): o.id for o in camp.orgs}
    garant_ids: list[int] = []
    helper_ids: list[int] = []
    unknown: list[str] = []
    seen: set[int] = set()

    def take(token: str, bucket: list[int]) -> None:
        oid = by_initials.get(token.casefold())
        if oid is None:
            unknown.append(token)
        elif oid not in seen:
            seen.add(oid)
            bucket.append(oid)

    items = [p for p in (text or "").replace("(", ",").replace(")", ",").split(",") if p.strip()]
    if items:
        for tok in _tokens(items[0], _GROUP_SEP):  # first comma-item → garants (split on + / space)
            take(tok, garant_ids)
        for item in items[1:]:                     # each later comma-item → helpers
            for tok in _tokens(item, _GROUP_SEP):
                take(tok, helper_ids)
    return garant_ids, helper_ids, unknown


def _color_to_category(camp: Camp, color_id: str | None) -> int | None:
    """The camp category whose color is nearest the Google event color, or None."""
    hex_color = google_client.color_id_to_hex(color_id)
    options = {c.id: c.color for c in camp.categories if c.color}
    return google_client.nearest_hex(hex_color, options)


def _detect(camp: Camp) -> list[dict]:
    """Diff the calendar against the camp's slots and return the reviewable changes.

    Keys off the `cpSlotId` marker we stamp on events we own. For a slot's event we
    surface, independently: a time move, a change of the slot's attendants (DESCRIPTION),
    a change of the activity's garants (LOCATION, activity-level → deduped) and a change of
    the activity's category (colorId, activity-level → deduped), plus deletions. A live
    event we don't own is a new_event import candidate, restricted to the camp's timeframe
    and to durations under 48h (longer = a whole-camp span event, ignored)."""
    events, _token = google_client.list_events(camp.google_calendar_id, None)  # full list
    tz = camp.timezone
    live = {e["id"]: e for e in events if e.get("status") != "cancelled"}
    mapped: set[str] = set()
    seen_garant: set[int] = set()
    seen_category: set[int] = set()
    changes: list[dict] = []

    for slot in (s for a in camp.activities for s in a.slots):
        if not slot.google_event_id:
            continue
        mapped.add(slot.google_event_id)
        ev = live.get(slot.google_event_id)
        if ev is None:
            changes.append({"kind": "deleted_in_google", "key": f"del:{slot.id}", "slot": slot})
            continue
        activity = slot.activity

        times = google_client.parse_event_times(ev, tz)
        if times and (times[0] != slot.start_at or times[1] != slot.end_at):
            changes.append({"kind": "time_change", "key": f"time:{slot.id}", "slot": slot,
                            "new_start": times[0], "new_end": times[1]})

        # attendants (slot-level) from DESCRIPTION
        att_ids, att_unknown = _match_initials(camp, ev.get("description"))
        if set(att_ids) != {a.org_id for a in slot.assignments}:
            changes.append({"kind": "attendants_change", "key": f"att:{slot.id}", "slot": slot,
                            "new_org_ids": att_ids, "new_initials": _initials(camp, att_ids),
                            "old_initials": _initials(camp, [a.org_id for a in slot.assignments]),
                            "unknown": att_unknown})

        # garants + helpers (activity-level) from LOCATION — one change per activity
        if activity.id not in seen_garant:
            gar_ids, help_ids, loc_unknown = _parse_location(camp, ev.get("location"))
            cur_gar = {a.org_id for a in activity.assignments if a.role == OrgRole.garant}
            cur_help = {a.org_id for a in activity.assignments if a.role == OrgRole.helper}
            if set(gar_ids) != cur_gar or set(help_ids) != cur_help:
                seen_garant.add(activity.id)
                changes.append({"kind": "garant_change", "key": f"gar:{activity.id}",
                                "activity": activity,
                                "new_garant_ids": gar_ids, "new_helper_ids": help_ids,
                                "new_garants": _initials(camp, gar_ids, sort=False),
                                "new_helpers": _initials(camp, help_ids, sort=False),
                                "old_garants": _initials(camp, list(cur_gar)),
                                "old_helpers": _initials(camp, list(cur_help)),
                                "unknown": loc_unknown})

        # category (activity-level) from colorId. Compare the colorId we *would* push to the
        # one Google currently has; they differ when the user recolored the event OR cleared
        # its color (→ no colorId → category becomes none).
        if activity.id not in seen_category:
            google_color = ev.get("colorId")
            if google_color != google_client.event_color_id(activity):
                new_cat = _color_to_category(camp, google_color)  # None when colour cleared
                if new_cat != activity.category_id:
                    seen_category.add(activity.id)
                    label = next((c.label for c in camp.categories if c.id == new_cat), "(bez kategorie)")
                    old_label = activity.category.label if activity.category else "(bez kategorie)"
                    changes.append({"kind": "category_change", "key": f"cat:{activity.id}",
                                    "activity": activity, "new_category_id": new_cat,
                                    "new_label": label, "old_label": old_label})

    window_start, window_end = camp_window(camp.start_date, camp.length_days, camp.window_start_min)
    own_slot_ids = {s.id for a in camp.activities for s in a.slots}
    # Events we've already queued for deletion (slot deleted here, delete op not yet drained, or
    # the push keeps failing). Their marker is our own now-gone slot id, so they'd otherwise look
    # "foreign" and be re-offered for import — re-creating the slot we just deleted. Skip them.
    pending_deletes = set(db.session.scalars(
        db.select(GoogleSyncOp.google_event_id).where(
            GoogleSyncOp.camp_id == camp.id, GoogleSyncOp.op == SyncOpKind.delete)
    ).all())
    for eid, ev in live.items():
        if eid in mapped or eid in pending_deletes:
            continue
        # An event tagged with a cpSlotId we don't own (another camp's, or a deleted slot)
        # is still importable — but flagged `foreign_slot` so the UI warns that the marker
        # will be rewritten. A marker that *is* one of our current slots is left alone.
        marker = ev.get("extendedProperties", {}).get("private", {}).get(google_client.SLOT_PROP)
        if marker and marker.isdigit() and int(marker) in own_slot_ids:
            continue
        times = google_client.parse_event_times(ev, tz)
        if times is None:
            continue  # all-day / malformed → not slot-shaped
        start, end = times
        if not (window_start <= start < window_end):
            continue  # outside the camp's timeframe
        if end - start > timedelta(hours=_MAX_IMPORT_HOURS):
            continue  # whole-camp span event, not a single program block
        gar_ids, help_ids, unk_loc = _parse_location(camp, ev.get("location"))
        att_ids, unk_desc = _match_initials(camp, ev.get("description"))
        changes.append({"kind": "new_event", "key": f"new:{eid}", "event_id": eid,
                        "summary": (ev.get("summary") or "(bez názvu)"),
                        "new_start": start, "new_end": end,
                        "location": ev.get("location", ""), "description": ev.get("description", ""),
                        "color_id": ev.get("colorId"),
                        "garant_initials": _initials(camp, gar_ids, sort=False),
                        "helper_initials": _initials(camp, help_ids, sort=False),
                        "attendant_initials": _initials(camp, att_ids, sort=False),
                        "category_id": _color_to_category(camp, ev.get("colorId")),  # by color
                        "foreign_slot": marker is not None,  # carried another camp's slot id
                        "unknown": list(dict.fromkeys(unk_loc + unk_desc))})

    changes.sort(key=_change_start)  # present the review chronologically
    return changes


def _change_start(c: dict) -> datetime:
    """The start datetime a change sorts by. Slot-level changes use their slot's start; an
    activity-level change (garant/category) uses the activity's earliest slot."""
    if "new_start" in c:               # new_event, time_change
        return c["new_start"]
    if "slot" in c:                    # attendants_change, deleted_in_google
        return c["slot"].start_at
    starts = [s.start_at for s in c["activity"].slots]  # garant_change, category_change
    return min(starts) if starts else datetime.max


def _serialize_change(c: dict) -> dict:
    """JSON-safe view of one change for the review UI (stable `key`, Czech `label`).
    `unknown` (when present) lists initials in Google that match no camp org."""
    kind = c["kind"]
    if kind == "new_event":
        return {"key": c["key"], "kind": kind, "summary": c["summary"],
                "label": f"Nová událost: {c['summary']}",
                "new_start": c["new_start"].isoformat(), "new_end": c["new_end"].isoformat(),
                "garant_initials": c["garant_initials"],
                "helper_initials": c["helper_initials"],
                "attendant_initials": c["attendant_initials"],
                "category_id": c["category_id"],              # inferred from the event color (or null)
                "foreign_slot": c["foreign_slot"],            # carried another camp's slot id
                "unknown": c["unknown"]}
    if kind == "garant_change":
        return {"key": c["key"], "kind": kind, "activity_title": c["activity"].title,
                "label": f"Změna garantů a pomocníků: {c['activity'].title}",
                "new_garants": c["new_garants"], "new_helpers": c["new_helpers"],
                "old_garants": c["old_garants"], "old_helpers": c["old_helpers"],
                "unknown": c["unknown"]}
    if kind == "category_change":
        return {"key": c["key"], "kind": kind, "activity_title": c["activity"].title,
                "label": f"Změna kategorie: {c['activity'].title}",
                "new_label": c["new_label"], "old_label": c["old_label"]}

    slot = c["slot"]
    out = {"key": c["key"], "kind": kind, "activity_title": slot.activity.title,
           "old_start": slot.start_at.isoformat(), "old_end": slot.end_at.isoformat()}
    if kind == "time_change":
        out["label"] = f"Změna času: {slot.activity.title}"
        out["new_start"] = c["new_start"].isoformat()
        out["new_end"] = c["new_end"].isoformat()
    elif kind == "attendants_change":
        out["label"] = f"Změna účastníků: {slot.activity.title}"
        out["new_initials"] = c["new_initials"]
        out["old_initials"] = c["old_initials"]
        out["unknown"] = c["unknown"]
    else:  # deleted_in_google
        out["label"] = f"Smazáno v Google: {slot.activity.title}"
    return out


def preview_pull(camp: Camp) -> dict:
    """Compute the reviewable list of Google→Planner changes plus the options the UI needs
    (existing activities to attach to, categories for a new activity). Read-only."""
    if not camp.google_calendar_id:
        raise errors.Invalid("Akce není připojená ke Google Calendar.")
    changes = _detect(camp)
    activities = sorted(camp.activities, key=lambda a: czech_sort_key(a.title))
    return {
        "rev": camp.timeline_rev,  # echoed back on apply to detect a racing timeline edit
        "changes": [_serialize_change(c) for c in changes],
        "activities": [{"id": a.id, "title": a.title} for a in activities],
        "categories": [{"id": c.id, "label": c.label, "color": c.color} for c in camp.categories],
    }


def apply_pull(camp: Camp, decisions: list[GooglePullDecisionIn], rev: int | None = None) -> dict:
    """Apply the user-selected subset of inbound changes. Re-detects against Google (so the
    client can't inject data) and acts only on changes whose key was chosen. New/attached
    events get their slot mapped and an upsert queued, so the next drain stamps the cpSlotId
    marker (no Google call happens inside this transaction). Owns its transaction.

    `rev` is the timeline_rev the review was computed against; a stale value means the
    timeline changed meanwhile (e.g. a concurrent editor) → Conflict (409), same optimistic
    lock as save_timeline, so an inbound apply can't silently clobber a racing slot edit."""
    if not camp.google_calendar_id:
        raise errors.Invalid("Akce není připojená ke Google Calendar.")
    if rev is not None and rev != camp.timeline_rev:
        raise errors.Conflict(
            "Časový plán se mezitím změnil. Načtěte změny z Google prosím znovu.",
            rev=camp.timeline_rev)
    chosen = {d.key: d for d in decisions}
    activity_ids = {a.id for a in camp.activities}
    category_ids = {c.id for c in camp.categories}
    applied = {"created_activities": 0, "imported_slots": 0, "updated": 0, "deleted": 0}

    for c in _detect(camp):
        decision = chosen.get(c["key"])
        if decision is None:
            continue
        kind = c["kind"]

        if kind == "time_change":
            slot = c["slot"]
            old_start, old_end = slot.start_at, slot.end_at
            slot.start_at, slot.end_at = c["new_start"], c["new_end"]
            audit.record(camp_id=camp.id, activity_id=slot.activity_id, entity_type=EntityType.slot,
                         entity_id=slot.id, action=AuditAction.update,
                         changes={"start_at": [old_start, slot.start_at], "end_at": [old_end, slot.end_at]})
            applied["updated"] += 1

        elif kind == "deleted_in_google":
            slot = c["slot"]
            audit.record(camp_id=camp.id, activity_id=slot.activity_id, entity_type=EntityType.slot,
                         entity_id=slot.id, action=AuditAction.delete,
                         changes={"start_at": [slot.start_at, None], "end_at": [slot.end_at, None]})
            db.session.delete(slot)
            applied["deleted"] += 1

        elif kind == "attendants_change":
            slot = c["slot"]
            before = _initials(camp, [a.org_id for a in slot.assignments])
            slot.assignments = [SlotAssignment(org_id=i) for i in c["new_org_ids"]]
            audit.record(camp_id=camp.id, activity_id=slot.activity_id, entity_type=EntityType.slot,
                         entity_id=slot.id, action=AuditAction.update,
                         changes={"orgs": [before, c["new_initials"]]})
            enqueue_upsert(camp, slot)  # reconcile (clears any unknown initials from the event)
            applied["updated"] += 1

        elif kind == "garant_change":
            activity = c["activity"]
            before_g = _initials(camp, [a.org_id for a in activity.assignments if a.role == OrgRole.garant])
            before_h = _initials(camp, [a.org_id for a in activity.assignments if a.role == OrgRole.helper])
            activity.assignments = (
                [ActivityAssignment(org_id=i, role=OrgRole.garant) for i in c["new_garant_ids"]]
                + [ActivityAssignment(org_id=i, role=OrgRole.helper) for i in c["new_helper_ids"]])
            audit.record(camp_id=camp.id, activity_id=activity.id, entity_type=EntityType.assignment,
                         entity_id=None, action=AuditAction.update,
                         changes={"garant": [before_g, _initials(camp, c["new_garant_ids"])],
                                  "helper": [before_h, _initials(camp, c["new_helper_ids"])]})
            for s in activity.slots:
                enqueue_upsert(camp, s)  # LOCATION is on every one of the activity's events
            applied["updated"] += 1

        elif kind == "category_change":
            activity = c["activity"]
            old = activity.category_id
            activity.category_id = c["new_category_id"]
            audit.record(camp_id=camp.id, activity_id=activity.id, entity_type=EntityType.activity,
                         entity_id=activity.id, action=AuditAction.update,
                         changes={"category_id": [old, activity.category_id]})
            for s in activity.slots:
                enqueue_upsert(camp, s)  # colorId is on every one of the activity's events
            applied["updated"] += 1

        else:  # new_event → create a new activity or attach to an existing one
            att_ids, _ = _match_initials(camp, c["description"])
            if decision.action == "attach":
                if decision.target_activity_id not in activity_ids:
                    raise errors.Invalid("Import: vybraná aktivita nepatří této akci.")
                activity = db.session.get(Activity, decision.target_activity_id)
            else:
                # Trust the chosen category (the preview already pre-fills the color-inferred
                # one); an explicit "bez kategorie" (null) or a foreign id → no category.
                category_id = decision.category_id if decision.category_id in category_ids else None
                activity = Activity(camp_id=camp.id, title=c["summary"][:255], category_id=category_id)
                db.session.add(activity)
                db.session.flush()
                audit.record(camp_id=camp.id, activity_id=activity.id, entity_type=EntityType.activity,
                             entity_id=activity.id, action=AuditAction.create,
                             changes={"title": [None, activity.title]})
                gar_ids, help_ids, _ = _parse_location(camp, c["location"])
                assignments = ([ActivityAssignment(org_id=i, role=OrgRole.garant) for i in gar_ids]
                               + [ActivityAssignment(org_id=i, role=OrgRole.helper) for i in help_ids])
                if assignments:  # seed the new activity's garants/helpers from LOCATION
                    activity.assignments = assignments
                applied["created_activities"] += 1

            slot = Slot(activity_id=activity.id, role=SlotRole.main, start_at=c["new_start"],
                        end_at=c["new_end"], google_event_id=c["event_id"],
                        assignments=[SlotAssignment(org_id=i) for i in att_ids])
            db.session.add(slot)
            db.session.flush()
            audit.record(camp_id=camp.id, activity_id=activity.id, entity_type=EntityType.slot,
                         entity_id=slot.id, action=AuditAction.create,
                         changes={"role": [None, slot.role.value], "start_at": [None, slot.start_at],
                                  "end_at": [None, slot.end_at]})
            enqueue_upsert(camp, slot)  # next drain stamps cpSlotId + aligns all fields
            applied["imported_slots"] += 1

    if any(applied.values()):
        bump_timeline_rev(camp)
        log.info("Google Calendar import applied (camp %s): created_activities=%d imported_slots=%d "
                 "updated=%d deleted=%d", camp.slug, applied["created_activities"],
                 applied["imported_slots"], applied["updated"], applied["deleted"])
    camp.google_last_pull_at = datetime.now()  # naive local — display metadata only
    db.session.commit()
    return {"applied": applied}
