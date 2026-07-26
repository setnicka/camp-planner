"""Camp create / settings-save and slug handling."""

from __future__ import annotations

import logging
import re

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from camp_planner.auth.permissions import can_view
from camp_planner.extensions import db
from camp_planner.models.audit import AuditAction, EntityType
from camp_planner.models.camp import Camp
from camp_planner.models.common import slugify
from camp_planner.schemas import CampUpdate
from camp_planner.services import audit, errors, google_client, google_sync, taxonomy
from camp_planner.services.timeline import bump_timeline_rev

log = logging.getLogger(__name__)

SNAP_CHOICES = (5, 10, 15, 30, 60)  # allowed editing-grid resolutions (also used by the forms)
# Fields whose change alters the timeline layout, so an in-flight edit must be invalidated.
_LAYOUT_FIELDS = {"start_date", "length_days", "window_start_min"}

# Czech per-field messages for the HTML form path (one message per field; the
# constraints themselves live in CampUpdate).
_FORM_ERRORS = {
    "name": "Název: vyplňte název akce.",
    "slug": "Slug: povolena jsou jen malá písmena bez diakritiky, číslice a pomlčky.",
    "start_date": "Začátek: zadejte datum ve tvaru RRRR-MM-DD.",
    "length_days": "Počet dní: musí být celé číslo, alespoň 1.",
    "window_start_min": "Začátek dne: zadejte čas ve tvaru HH:MM.",
    "snap_minutes": f"Krok mřížky: povolené hodnoty {list(SNAP_CHOICES)}.",
    "timezone": "Časové pásmo: neznámé pásmo.",
    "latitude": "Zeměpisná šířka: musí být číslo v rozsahu -90 až 90.",
    "longitude": "Zeměpisná délka: musí být číslo v rozsahu -180 až 180.",
}


def validate_camp_form(form, *, require_meta: bool = True) -> tuple[dict, list[str]]:
    """Validate a posted HTML create/edit form into a cleaned dict + a list of Czech
    error strings (for inline re-rendering), by running the same CampUpdate schema
    the JSON API uses. Slug uniqueness is checked at commit (race-safe), not here.

    require_meta includes name/slug — true for create and admin edit; editors submit
    the form without them."""
    def text(field: str) -> str:
        return (form.get(field) or "").strip()

    snap = text("snap_minutes")
    win = text("window_start_min")
    if re.fullmatch(r"\d{2}:\d{2}", win):  # <input type="time"> posts HH:MM
        win = int(win[:2]) * 60 + int(win[3:])
    raw: dict = {
        "start_date": text("start_date"),
        "length_days": text("length_days"),
        "window_start_min": win,
        "snap_minutes": int(snap) if snap.isdigit() else snap,  # Literal[] won't coerce strings
        "timezone": text("timezone") or "Europe/Prague",
        "latitude": text("latitude") or None,    # blank coordinate → not set
        "longitude": text("longitude") or None,
    }
    if require_meta:
        raw["name"] = text("name")
        raw["slug"] = text("slug") or slugify(raw["name"])
    try:
        parsed = CampUpdate.model_validate(raw)
    except ValidationError as exc:
        problems: list[str] = []
        for err in exc.errors():
            field = str(err["loc"][0]) if err["loc"] else "?"
            message = _FORM_ERRORS.get(field, f"{field}: neplatná hodnota.")
            if message not in problems:
                problems.append(message)
        return {}, problems
    return parsed.model_dump(exclude_unset=True), []


def create_camp(data: dict, *, copy_from_slug: str | None = None, copy_parts=None) -> Camp:
    """Create a camp + audit, in one transaction. A blank slug defaults to
    slugify(name); copy_from_slug copies the chosen taxonomy parts from that camp.
    Raises errors.Invalid on a bad copy source or a slug collision."""
    source = None
    if copy_from_slug:
        source = db.session.scalar(db.select(Camp).filter_by(slug=copy_from_slug))
        if source is None or not can_view(source):
            raise errors.Invalid("Převzít z akce: vyberte platnou akci.")
    slug = data.get("slug") or slugify(data["name"])
    camp = Camp(
        name=data["name"],
        slug=slug,
        start_date=data["start_date"],
        length_days=data["length_days"],
        timezone=data["timezone"],
        window_start_min=data["window_start_min"],
        snap_minutes=data["snap_minutes"],
        latitude=data.get("latitude"),
        longitude=data.get("longitude"),
    )
    db.session.add(camp)
    try:
        db.session.flush()  # assign camp.id; a dup slug raises here, before copy_into runs
        if source is not None:
            taxonomy.copy_into(camp, source, parts=copy_parts)
        audit.record(
            camp_id=camp.id,
            entity_type=EntityType.camp,
            entity_id=camp.id,
            action=AuditAction.create,
            changes={"name": [None, camp.name], "slug": [None, camp.slug]},
        )
        db.session.commit()
    except IntegrityError:  # dup slug at flush, or a race lost at commit
        db.session.rollback()
        raise errors.Invalid(f"Slug „{slug}“ už používá jiná akce.") from None
    return camp


def delete_camp(camp: Camp) -> dict:
    """Delete a camp (cascades to its taxonomy, materials and audit trail). Refused while
    it still has activities, so a populated camp can't be wiped by accident. The caller
    enforces admin rights; this enforces only the empty-camp rule. No audit row is written
    — it would be cascade-deleted with the camp."""
    if camp.activities:
        raise errors.Invalid("Akci nelze smazat – nejprve odstraňte všechny její aktivity.")
    camp_id = camp.id
    db.session.delete(camp)
    db.session.commit()
    return {"id": camp_id}


def _all_slots(camp: Camp):
    return (slot for activity in camp.activities for slot in activity.slots)


def _calendar_conflict(calendar_id: str, start, end, exclude_camp_id: int) -> Camp | None:
    """Another camp connected to the same calendar whose time window overlaps [start, end),
    or None. One calendar may be shared by several camps, but only if their dates don't
    overlap (events from different camps would otherwise collide on the calendar)."""
    others = db.session.scalars(
        db.select(Camp).where(Camp.google_calendar_id == calendar_id, Camp.id != exclude_camp_id)
    ).all()
    for other in others:
        o_start, o_end = google_sync.camp_window(other.start_date, other.length_days,
                                                 other.window_start_min)
        if start < o_end and o_start < end:  # half-open intervals overlap
            return other
    return None


def _owned_events(calendar_id: str) -> dict[str, str]:
    """Map of {slot id (as str) -> event id} for events on the calendar that we previously
    created (they carry the cpSlotId marker). Lets connect adopt them instead of inserting
    duplicates."""
    owned: dict[str, str] = {}
    events = google_client.list_events(calendar_id)
    for ev in events:
        if ev.get("status") == "cancelled":
            continue
        sid = ev.get("extendedProperties", {}).get("private", {}).get(google_client.SLOT_PROP)
        if sid:
            owned[sid] = ev["id"]
    return owned


def set_google_calendar(camp: Camp, calendar_id: str) -> dict:
    """Connect the camp to a Google calendar (by id). Verifies the service account can reach
    it, then queues an export of the whole current schedule (drained out of band). Events we
    previously created on this calendar (tagged with cpSlotId) are adopted rather than
    re-inserted, so reconnecting doesn't duplicate them. Connecting to the calendar the camp
    is already on is a no-op. Owns its transaction."""
    if not google_client.is_configured():
        raise errors.Invalid("Google Calendar není v této instalaci nastavený.")
    calendar_id = (calendar_id or "").strip()
    if not calendar_id:
        raise errors.Invalid("Zadejte ID kalendáře.")
    if calendar_id == camp.google_calendar_id:
        return {"google": google_status(camp)}

    start, end = google_sync.camp_window(camp.start_date, camp.length_days, camp.window_start_min)
    conflict = _calendar_conflict(calendar_id, start, end, camp.id)
    if conflict:
        raise errors.Invalid(
            f"Tento kalendář už ve stejném termínu používá akce „{conflict.name}“. Jeden kalendář "
            f"může sdílet více akcí, ale jejich termíny se nesmí překrývat.")

    google_client.verify_access(calendar_id)
    owned = _owned_events(calendar_id)
    was = camp.google_calendar_id
    camp.google_calendar_id = calendar_id
    for slot in _all_slots(camp):
        slot.google_event_id = owned.get(str(slot.id))  # adopt our existing event, else None → insert
        google_sync.enqueue_upsert(camp, slot)
    audit.record(camp_id=camp.id, entity_type=EntityType.camp, entity_id=camp.id,
                 action=AuditAction.update, changes={"google_calendar_id": [was, calendar_id]})
    db.session.commit()
    log.info("Google Calendar connected: camp %s → calendar %s (%d events queued, %d adopted)",
             camp.slug, calendar_id, google_sync.pending_count(camp), len(owned))
    return {"google": google_status(camp)}


def disconnect_google(camp: Camp) -> dict:
    """Disconnect the camp from Google: forget the calendar, the event mapping and any
    queued ops. Events already in Google are left in place. Owns its transaction."""
    if not camp.google_calendar_id:
        return {"google": google_status(camp)}
    old = camp.google_calendar_id
    camp.google_calendar_id = None
    camp.google_last_pull_at = None
    for slot in _all_slots(camp):
        slot.google_event_id = None
    for op in list(camp.sync_ops):
        db.session.delete(op)
    audit.record(camp_id=camp.id, entity_type=EntityType.camp, entity_id=camp.id,
                 action=AuditAction.update, changes={"google_calendar_id": [old, None]})
    db.session.commit()
    log.info("Google Calendar disconnected: camp %s (was calendar %s)", camp.slug, old)
    return {"google": google_status(camp)}


def google_status(camp: Camp) -> dict:
    """Connection status for the settings UI / API. `enabled` reflects whether the
    deployment is configured at all; `service_account_email` is the address to share a
    calendar with."""
    enabled = google_client.is_configured()
    connected = bool(camp.google_calendar_id)
    failed, last_error = google_sync.failure_summary(camp) if connected else (0, None)
    return {
        "enabled": enabled,
        "service_account_email": google_client.service_account_email() if enabled else None,
        "calendar_id": camp.google_calendar_id,
        "connected": connected,
        "pending_ops": google_sync.pending_count(camp) if connected else 0,
        "failed_ops": failed,        # ops that have failed at least once (e.g. read-only share)
        "last_error": last_error,    # most recent push error, for the UI to show
        "last_pull_at": camp.google_last_pull_at.isoformat() if camp.google_last_pull_at else None,
    }


def save_camp_settings(camp: Camp, data: dict, *, allow_meta: bool) -> None:
    """Apply settings to a camp, recording a field-level diff and bumping the
    timeline revision if a layout field changed. name/slug are applied only when
    allow_meta (admin) — never trust which fields the form submitted."""
    # If the camp shares a Google calendar, refuse a date change that would overlap another
    # camp on that calendar (checked before applying anything, so a reject leaves it intact).
    if camp.google_calendar_id and any(
            f in data and data[f] != getattr(camp, f) for f in _LAYOUT_FIELDS):
        start, end = google_sync.camp_window(
            data.get("start_date", camp.start_date),
            data.get("length_days", camp.length_days),
            data.get("window_start_min", camp.window_start_min))
        conflict = _calendar_conflict(camp.google_calendar_id, start, end, camp.id)
        if conflict:
            raise errors.Invalid(
                f"Změna termínu by se na sdíleném Google kalendáři překryla s akcí "
                f"„{conflict.name}“. Termíny akcí na jednom kalendáři se nesmí překrývat.")

    fields = ["start_date", "length_days", "timezone", "window_start_min", "snap_minutes",
              "latitude", "longitude"]
    if allow_meta:
        fields = ["name", "slug", *fields]

    # A sent null means "unchanged", except the nullable coordinates where it clears.
    changes = audit.apply_changes(camp, {
        f: data[f] for f in fields
        if f in data and not (data[f] is None and f not in ("latitude", "longitude"))})

    if not changes:
        return

    if changes.keys() & _LAYOUT_FIELDS:
        bump_timeline_rev(camp)
    audit.record(
        camp_id=camp.id,
        entity_type=EntityType.camp,
        entity_id=camp.id,
        action=AuditAction.update,
        changes=changes,
    )
    db.session.commit()
