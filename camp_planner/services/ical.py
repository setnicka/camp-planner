"""iCal feed of a camp's slots, for external calendar apps.

The feed reuses the timeline's filter grammar (repeated ?filter=type:value params,
the same tokens as the #filter= hash): values OR within a type, types AND together.

Slot datetimes are naive camp-local wall times, converted to UTC per instant at
export (no VTIMEZONE needed). A deliberate exception to the "never convert through
ZoneInfo" rule: an export must hand external clients real instants.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from camp_planner.models.activity import OrgRole
from camp_planner.models.common import czech_sort_key
from camp_planner.services import errors
from camp_planner.services.timeline import NO_CATEGORY, slot_visible
from camp_planner.version import __version__

if TYPE_CHECKING:
    from collections.abc import Iterable

    from camp_planner.models.camp import Camp
    from camp_planner.models.org import Org

# garant/attending/activity values are ids; category values are keys ('_none' = no category).
_FILTER_TYPES = ("activity", "category", "garant", "attending")


def parse_filters(values: Iterable[str]) -> dict[str, set]:
    """Parse filter=type:value tokens into {type: set-of-values}: OR within a type, AND
    across types (see build_feed). Raises Invalid on an unknown type or malformed token;
    an empty iterable means no filtering."""
    filters: dict[str, set] = {}
    for raw in values:
        ftype, _, value = raw.partition(":")
        if not (value and ftype in _FILTER_TYPES and (ftype == "category" or value.isdigit())):
            raise errors.Invalid(f"Neplatný filtr „{raw}“.")
        filters.setdefault(ftype, set()).add(value if ftype == "category" else int(value))
    return filters


# --- RFC 5545 plumbing -----------------------------------------------------------------

def _escape(text: str) -> str:
    """TEXT value escaping: backslash, semicolon, comma, newlines."""
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """Fold a content line to 75-octet chunks (continuations start with a space and so
    carry 74), cutting only on UTF-8 character boundaries."""
    raw = line.encode()
    if len(raw) <= 75:
        return line
    parts, limit = [], 75
    while raw:
        if len(raw) <= limit:
            parts.append(raw)
            break
        cut = limit
        while raw[cut] & 0xC0 == 0x80:  # inside a multibyte sequence: back off
            cut -= 1
        parts.append(raw[:cut])
        raw = raw[cut:]
        limit = 74
    return "\r\n ".join(p.decode() for p in parts)


def _by_initials(org: Org) -> tuple[str, str]:
    """The timeline's org order."""
    return czech_sort_key(org.initials)


_UTC_FMT = "%Y%m%dT%H%M%SZ"


def _utc(local: datetime, tz: ZoneInfo) -> str:
    """Wall times inside a DST transition hour are ambiguous in the model; fold=0
    (first occurrence) is the convention."""
    return local.replace(tzinfo=tz).astimezone(timezone.utc).strftime(_UTC_FMT)


def build_feed(camp: Camp, filters: dict[str, set]) -> str:
    """The camp's matching slots as a VCALENDAR string (CRLF lines, folded)."""
    tz = ZoneInfo(camp.timezone)
    stamp = datetime.now(timezone.utc).strftime(_UTC_FMT)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//Camp Planner {__version__}//CS",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:" + _escape(camp.name),
    ]
    act_w, cat_w, gar_w, att_w = (filters.get(k) for k in _FILTER_TYPES)
    events: list[tuple[datetime, int, list[str]]] = []
    for activity in camp.activities:
        if not activity.slots:
            continue
        if act_w and activity.id not in act_w:
            continue
        cat_key = activity.category.key if activity.category else NO_CATEGORY
        if cat_w and cat_key not in cat_w:
            continue
        garants = [a.org for a in activity.assignments if a.role is OrgRole.garant]
        helpers = [a.org for a in activity.assignments if a.role is OrgRole.helper]
        # the garant facet spans garants and helpers, like the timeline chips
        if gar_w and not any(o.id in gar_w for o in (*garants, *helpers)):
            continue
        garants.sort(key=_by_initials)
        helpers.sort(key=_by_initials)
        base_desc = [label + ": " + ", ".join(o.name for o in group)
                     for label, group in (("Garant", garants), ("Pomocníci", helpers)) if group]
        category = "CATEGORIES:" + _escape(activity.category.label) if activity.category else None
        for slot in activity.slots:
            # export exactly what the timeline renders (clipped straddlers stay,
            # orphaned slots don't)
            if not slot_visible(camp, slot):
                continue
            if att_w and not any(a.org_id in att_w for a in slot.assignments):
                continue
            attending = sorted((a.org for a in slot.assignments), key=_by_initials)
            desc = base_desc + (["Účast: " + ", ".join(o.name for o in attending)]
                                if attending else [])
            event = [
                "BEGIN:VEVENT",
                # camp id, not slug: UIDs must survive a slug rename
                f"UID:slot-{slot.id}@camp-{camp.id}.camp-planner",
                "DTSTAMP:" + stamp,
                "DTSTART:" + _utc(slot.start_at, tz),
                "DTEND:" + _utc(slot.end_at, tz),
                "SUMMARY:" + _escape(slot.calendar_summary),
            ]
            if category:
                event.append(category)
            if desc:
                event.append("DESCRIPTION:" + _escape("\n".join(desc)))
            event.append("END:VEVENT")
            events.append((slot.start_at, slot.id, event))
    events.sort()   # (start_at, id) is unique, so the tuple compare never reaches the lines
    for _start, _id, event in events:
        lines.extend(event)
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
