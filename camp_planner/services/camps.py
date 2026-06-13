"""Camp create / settings-save and slug handling."""

from __future__ import annotations

import logging
import re
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.exc import IntegrityError

from camp_planner.extensions import db
from camp_planner.models.audit import AuditAction, EntityType
from camp_planner.models.camp import Camp
from camp_planner.models.common import strip_diacritics
from camp_planner.services import audit, errors, google_client, google_sync
from camp_planner.services.timeline import bump_timeline_rev

log = logging.getLogger(__name__)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_SLUG_OK = re.compile(r"^[a-z0-9-]+$")
SNAP_CHOICES = (5, 10, 15, 30, 60)  # allowed editing-grid resolutions (also used by the forms)
# Fields whose change alters the timeline layout, so an in-flight edit must be invalidated.
_LAYOUT_FIELDS = {"start_date", "length_days", "window_start_min"}


def slugify(name: str) -> str:
    """Lowercase, strip diacritics, collapse non-alphanumeric runs to single
    hyphens, trim. Capped at the column's 80 chars."""
    return _SLUG_STRIP.sub("-", strip_diacritics(name.lower())).strip("-")[:80]


def _parse_int(value: str | None, label: str, errors: list[str]) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        errors.append(f"{label}: musí být celé číslo.")
        return None


def _parse_coord(value: str | None, label: str, lo: float, hi: float, errors: list[str]) -> float | None:
    """Optional geographic coordinate from a form field: blank -> None, else a float
    within [lo, hi]."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        num = float(text)
    except ValueError:
        errors.append(f"{label}: musí být číslo.")
        return None
    if not lo <= num <= hi:
        errors.append(f"{label}: mimo rozsah {lo}–{hi}.")
        return None
    return num


def validate_camp_form(form, *, require_meta: bool = True) -> tuple[dict, list[str]]:
    """Validate a posted HTML create/edit form into a cleaned dict + a list of Czech
    error strings (for inline re-rendering). This is the web path's validator; the
    JSON API validates the same fields via the CampCreate/CampUpdate pydantic schemas.
    Slug uniqueness is checked at commit (race-safe), not here.

    require_meta validates (and includes) name/slug — true for create and admin edit.
    Editors who can't change name/slug submit the form without them, so pass false and
    those fields are simply skipped (no placeholder inputs needed)."""
    errors: list[str] = []
    data: dict = {}

    if require_meta:
        data["name"] = (form.get("name") or "").strip()
        if not data["name"]:
            errors.append("Název: vyplňte název akce.")
        slug = (form.get("slug") or "").strip() or slugify(data["name"])
        if not _SLUG_OK.match(slug) or len(slug) > 80:
            errors.append("Slug: povolena jsou jen malá písmena bez diakritiky, číslice a pomlčky.")
        data["slug"] = slug

    raw_date = (form.get("start_date") or "").strip()
    try:
        data["start_date"] = date.fromisoformat(raw_date)
    except ValueError:
        errors.append("Začátek: zadejte datum ve tvaru RRRR-MM-DD.")

    length = _parse_int(form.get("length_days"), "Počet dní", errors)
    if length is not None:
        if length < 1:
            errors.append("Počet dní: musí být alespoň 1.")
        data["length_days"] = length

    window = _parse_int(form.get("window_start_min"), "Začátek dne (min)", errors)
    if window is not None:
        if not 0 <= window < 1440:
            errors.append("Začátek dne: musí být 0–1439 minut.")
        data["window_start_min"] = window

    snap = _parse_int(form.get("snap_minutes"), "Krok mřížky", errors)
    if snap is not None:
        if snap not in SNAP_CHOICES:
            errors.append(f"Krok mřížky: povolené hodnoty {list(SNAP_CHOICES)}.")
        data["snap_minutes"] = snap

    tz = (form.get("timezone") or "Europe/Prague").strip()
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        errors.append(f"Časové pásmo: neznámé pásmo {tz!r}.")
    data["timezone"] = tz

    data["latitude"] = _parse_coord(form.get("latitude"), "Zeměpisná šířka", -90, 90, errors)
    data["longitude"] = _parse_coord(form.get("longitude"), "Zeměpisná délka", -180, 180, errors)

    return data, errors


def create_camp(data: dict, *, copy_from: Camp | None = None, copy_parts=None) -> Camp | None:
    """Create a camp + audit, in one transaction. A new camp starts empty (no
    categories/orgs/tags); when copy_from is given, the chosen taxonomy parts are
    copied from it (see copy_parts). Returns None (and rolls back) on a slug
    collision at commit."""
    camp = Camp(
        name=data["name"],
        slug=data["slug"],
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
        if copy_from is not None:
            # Lazy import: taxonomy imports slugify from this module.
            from camp_planner.services import taxonomy
            taxonomy.copy_into(camp, copy_from, parts=copy_parts)
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
        return None
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


def _owned_events(calendar_id: str) -> dict[str, str]:
    """Map of {slot id (as str) -> event id} for events on the calendar that we previously
    created (they carry the cpSlotId marker). Lets connect adopt them instead of inserting
    duplicates."""
    owned: dict[str, str] = {}
    events, _token = google_client.list_events(calendar_id, None)
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

    google_client.verify_access(calendar_id)
    owned = _owned_events(calendar_id)
    was = camp.google_calendar_id
    camp.google_calendar_id = calendar_id
    camp.google_sync_token = None  # a different calendar → start its inbound cursor fresh
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
    camp.google_sync_token = None
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
    }


def save_camp_settings(camp: Camp, data: dict, *, allow_meta: bool) -> None:
    """Apply settings to a camp, recording a field-level diff and bumping the
    timeline revision if a layout field changed. name/slug are applied only when
    allow_meta (admin) — never trust which fields the form submitted."""
    fields = ["start_date", "length_days", "timezone", "window_start_min", "snap_minutes",
              "latitude", "longitude"]
    if allow_meta:
        fields = ["name", "slug", *fields]

    changes: dict[str, list] = {}
    layout_changed = False
    for field in fields:
        if field not in data:
            continue
        old = getattr(camp, field)
        new = data[field]
        if old == new:
            continue
        changes[field] = [old, new]
        setattr(camp, field, new)
        layout_changed = layout_changed or field in _LAYOUT_FIELDS

    if not changes:
        return

    if layout_changed:
        bump_timeline_rev(camp)
    audit.record(
        camp_id=camp.id,
        entity_type=EntityType.camp,
        entity_id=camp.id,
        action=AuditAction.update,
        changes=changes,
    )
    db.session.commit()
