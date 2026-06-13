"""The Google Calendar boundary — the ONLY module that talks to Google's API and the
ONLY place datetimes cross the naive/aware line.

Slot datetimes are naive local wall-clock in camp.timezone (see models/slot.py); Google
wants an explicit time zone. We hand Google `{dateTime: <naive isoformat>, timeZone:
<camp.timezone>}` on the way out, and convert Google's offset-aware times back to naive
camp-local on the way in (`parse_event_times`). zoneinfo is used here and nowhere else.

The google-api-python-client / google-auth imports are deferred into the functions so the
heavy client libraries only load when sync actually runs — the rest of the app, and the
test suite (which monkeypatches this module), never pay for them. The feature is enabled
only when GOOGLE_SERVICE_ACCOUNT_JSON is configured; see docs/google_calendar_setup.md.
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app

from camp_planner.services import errors

if TYPE_CHECKING:
    from camp_planner.models.slot import Slot

SCOPES = ["https://www.googleapis.com/auth/calendar"]
# Private extended-property key marking events we own, so the inbound review can tell a
# Planner-created event (a time-change / deletion candidate) from a user-created one (an
# import candidate). Holds the originating slot id as a string.
SLOT_PROP = "cpSlotId"

_ROLE_SUFFIX = {"prep": " (příprava)", "cleanup": " (úklid)"}


def is_configured() -> bool:
    """True when the deployment has a service-account key, i.e. the feature is enabled."""
    return bool(current_app.config.get("GOOGLE_SERVICE_ACCOUNT_JSON"))


def _service_account_info() -> dict:
    """Parse the configured key, accepting either inline JSON or a path to the JSON file."""
    raw = current_app.config.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise errors.Invalid("Google Calendar není v této instalaci nastavený.")
    text = raw.strip()
    if not text.startswith("{"):
        with open(text, encoding="utf-8") as fh:
            text = fh.read()
    return json.loads(text)


def service_account_email() -> str:
    """The address a user must share their calendar with (shown in the connect UI)."""
    return _service_account_info().get("client_email", "")


@lru_cache(maxsize=1)
def _build_service(info_json: str):
    from google.oauth2 import service_account  # noqa: PLC0415 — deferred optional import
    from googleapiclient.discovery import build  # noqa: PLC0415

    creds = service_account.Credentials.from_service_account_info(
        json.loads(info_json), scopes=SCOPES
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def client():
    """A cached Google Calendar API service built from the service-account key."""
    return _build_service(json.dumps(_service_account_info(), sort_keys=True))


# --- payload building / parsing (the timezone boundary) -------------------------------

def initials_csv(initials: list[str]) -> str:
    """Czech-sorted, comma-joined initials — the DESCRIPTION (attendants) wire format."""
    from camp_planner.models.common import czech_sort_key  # noqa: PLC0415

    return ", ".join(sorted(initials, key=czech_sort_key))


def format_location(garants: list[str], helpers: list[str]) -> str:
    """The LOCATION wire format: the garants joined by '+' as the first comma item, then
    each helper as its own comma item — e.g. "K+M, P". Each group is czech-sorted."""
    from camp_planner.models.common import czech_sort_key  # noqa: PLC0415

    g = sorted(garants, key=czech_sort_key)
    h = sorted(helpers, key=czech_sort_key)
    parts = (["+".join(g)] if g else []) + h
    return ", ".join(parts)


def event_body(slot: Slot) -> dict:
    """Build the full Google event payload mirroring a slot. Field mapping:
    - summary     ← activity title (+ role suffix for prep/cleanup)
    - location    ← garants (joined by '+') then helpers, e.g. "K+M, P" (format_location)
    - description ← the slot's attendant orgs (initials)
    - colorId     ← the activity's category colour snapped to the palette (omitted if none)
    start/end are the slot's naive local times tagged with the camp tz."""
    from camp_planner.models.activity import OrgRole  # noqa: PLC0415

    activity = slot.activity
    camp = activity.camp
    garants = [a.org.initials for a in activity.assignments if a.role == OrgRole.garant]
    helpers = [a.org.initials for a in activity.assignments if a.role == OrgRole.helper]
    attendants = [a.org.initials for a in slot.assignments]
    body = {
        "summary": f"{activity.title}{_ROLE_SUFFIX.get(slot.role.value, '')}",
        "location": format_location(garants, helpers),
        "description": initials_csv(attendants),
        "start": {"dateTime": slot.start_at.isoformat(), "timeZone": camp.timezone},
        "end": {"dateTime": slot.end_at.isoformat(), "timeZone": camp.timezone},
        "extendedProperties": {"private": {SLOT_PROP: str(slot.id)}},
    }
    color_id = event_color_id(activity)
    if color_id:
        body["colorId"] = color_id
    return body


# --- colors (category color ↔ Google's fixed 11-color event palette) ------------------

def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    h = value.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def nearest_hex(target: str | None, options: dict) -> object | None:
    """The key in `options` (a {key: '#rrggbb'} map) whose color is closest to target
    (squared RGB distance). None if target is falsy or options empty."""
    if not target or not options:
        return None
    t = _hex_to_rgb(target)
    return min(options, key=lambda k: sum((a - b) ** 2 for a, b in zip(t, _hex_to_rgb(options[k]))))


# Google Calendar's modern event-color palette (colorId → the hex the UI actually shows).
# We deliberately do NOT use colors().get(): its "event" palette returns the *older* pastel
# backgrounds (e.g. Peacock = #46d6db), which don't match what users see, so snapping an
# exact modern color like #039BE5 would miss and land on a neighbour (e.g. Blueberry).
_EVENT_COLORS = {
    "1": "#7986CB",   # Lavender
    "2": "#33B679",   # Sage
    "3": "#8E24AA",   # Grape
    "4": "#E67C73",   # Flamingo
    "5": "#F6BF26",   # Banana
    "6": "#F4511E",   # Tangerine
    "7": "#039BE5",   # Peacock
    "8": "#616161",   # Graphite
    "9": "#3F51B5",   # Blueberry
    "10": "#0B8043",  # Basil
    "11": "#D50000",  # Tomato
}


def color_palette() -> dict[str, str]:
    """Google's fixed event-color palette as {colorId: hex}, matching the modern UI swatches."""
    return _EVENT_COLORS


def nearest_color_id(hex_color: str | None) -> str | None:
    """The Google event colorId whose swatch is closest to an arbitrary hex color."""
    return nearest_hex(hex_color, color_palette())


def color_id_to_hex(color_id: str | None) -> str | None:
    return color_palette().get(color_id) if color_id else None


def event_color_id(activity) -> str | None:
    """colorId for an activity's event: its category color snapped to the palette (or None)."""
    cat = activity.category
    return nearest_color_id(cat.color) if cat is not None and cat.color else None


def parse_event_times(event: dict, timezone: str) -> tuple[datetime, datetime] | None:
    """Google event start/end → naive wall-clock datetimes in the camp timezone.

    Returns None for all-day events (date, not dateTime) — those aren't slot-shaped and
    are skipped by the inbound review. An offset-less dateTime is localized with the event
    field's own `timeZone` when present, otherwise treated as already camp-local."""
    start, end = event.get("start", {}), event.get("end", {})
    start_dt, end_dt = start.get("dateTime"), end.get("dateTime")
    if not start_dt or not end_dt:
        return None
    camp_tz = ZoneInfo(timezone)
    return (_to_local(start_dt, start.get("timeZone"), camp_tz),
            _to_local(end_dt, end.get("timeZone"), camp_tz))


def _to_local(rfc3339: str, source_tz: str | None, camp_tz: ZoneInfo) -> datetime:
    """Parse an RFC3339 timestamp and return it as naive wall-clock in camp_tz. A value with
    an explicit offset is converted directly; an offset-less one is localized with source_tz
    (the event field's own timeZone) when given, else treated as already camp-local."""
    aware = datetime.fromisoformat(rfc3339)
    if aware.tzinfo is None:
        if not source_tz:
            return aware  # truly floating → already camp-local wall-clock
        try:
            aware = aware.replace(tzinfo=ZoneInfo(source_tz))
        except (ZoneInfoNotFoundError, ValueError):
            return aware  # unknown tz → best effort: treat as camp-local
    return aware.astimezone(camp_tz).replace(tzinfo=None)


# --- calendar / event operations ------------------------------------------------------

def verify_access(calendar_id: str) -> None:
    """Check the service account can read the calendar; raise Invalid (→400) otherwise."""
    from googleapiclient.errors import HttpError  # noqa: PLC0415

    try:
        client().events().list(calendarId=calendar_id, maxResults=1).execute()
    except HttpError as exc:
        if exc.resp.status in (403, 404):
            raise errors.Invalid(
                "Kalendář není přístupný. Zkontrolujte ID kalendáře a že je sdílený se "
                f"service accountem {service_account_email()} s právem měnit události."
            ) from exc
        raise errors.Invalid(f"Google Calendar: chyba {exc.resp.status}.") from exc


def insert_event(calendar_id: str, body: dict) -> str:
    """Create an event; return its Google id."""
    created = client().events().insert(calendarId=calendar_id, body=body).execute()
    return created["id"]


def patch_event(calendar_id: str, event_id: str, body: dict) -> None:
    client().events().patch(calendarId=calendar_id, eventId=event_id, body=body).execute()


def delete_event(calendar_id: str, event_id: str) -> None:
    """Delete an event, treating an already-gone event (404/410) as success."""
    from googleapiclient.errors import HttpError  # noqa: PLC0415

    try:
        client().events().delete(calendarId=calendar_id, eventId=event_id).execute()
    except HttpError as exc:
        if exc.resp.status not in (404, 410):
            raise


def list_events(calendar_id: str, sync_token: str | None) -> tuple[list[dict], str | None]:
    """List events for the inbound review. With a sync_token, return only changes since it
    (incremental); without, a full single-events listing. Returns (events, next_sync_token).
    A 410 (expired token) is signalled by raising errors.Invalid so the caller can full-sync."""
    from googleapiclient.errors import HttpError  # noqa: PLC0415

    svc = client().events()
    params: dict = {"calendarId": calendar_id, "showDeleted": True}
    if sync_token:
        params["syncToken"] = sync_token
    else:
        params["singleEvents"] = True

    events: list[dict] = []
    page_token = None
    try:
        while True:
            if page_token:
                params["pageToken"] = page_token
            resp = svc.list(**params).execute()
            events.extend(resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                return events, resp.get("nextSyncToken")
    except HttpError as exc:
        if exc.resp.status == 410:  # sync token expired → caller should retry full
            raise errors.Invalid("__SYNC_TOKEN_EXPIRED__") from exc
        raise
