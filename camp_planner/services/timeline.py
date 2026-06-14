"""Build the read-only timeline payload and manage the optimistic-lock token.

The camp "day" is a 24h window starting at camp.window_start_min (e.g. 04:00), so
a night program stays on one row. A slot whose [start_at, end_at) crosses a window
boundary is sliced into one segment per day-row it touches, carrying continuation
flags. This is a direct port of the mockup's buildSegments/absMin (docs/mockups/data.js).

IMPORTANT: slot datetimes are naive local values. All math here is pure calendar
arithmetic on those values — do NOT convert through ZoneInfo. camp.timezone is
display metadata only; introducing tz conversion would reintroduce the DST shifts
the model deliberately avoids.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from camp_planner.models.activity import OrgRole
from camp_planner.models.common import czech_sort_key
from camp_planner.schemas import (
    TimelineCamp,
    TimelineCategory,
    TimelineGroup,
    TimelineOrg,
    TimelinePayload,
    TimelineSegment,
    TimelineTag,
)

if TYPE_CHECKING:
    from datetime import date

    from camp_planner.models.camp import Camp

DAY_MIN = 24 * 60


def bump_timeline_rev(camp: Camp) -> None:
    """Invalidate any in-flight timeline edit (optimistic lock). Call on every
    change to slot placement or the day window/date fields."""
    camp.timeline_rev = (camp.timeline_rev or 0) + 1


def _abs_min(dt: datetime, start_date: date) -> int:
    """Minutes from camp origin (midnight of start_date). Pure calendar math."""
    return (dt.date() - start_date).days * DAY_MIN + dt.hour * 60 + dt.minute


def slice_segments(
    s_abs: int, e_abs: int, window_start: int, length_days: int
) -> list[dict]:
    """Slice an absolute-minute span into per-day-row segments (window-relative).

    Offset by window_start: the window runs e.g. 04:00->04:00, so an after-midnight
    time is an offset of 24:00–28:00 and must still map to THIS camp day, not the
    next. Integer minutes are exact, so boundaries need no epsilon fudge.
    """
    first = max(0, (s_abs - window_start) // DAY_MIN)
    last = min(length_days - 1, (e_abs - 1 - window_start) // DAY_MIN)
    segments = []
    for day in range(first, last + 1):
        win_lo = day * DAY_MIN + window_start
        win_hi = win_lo + DAY_MIN
        lo = max(s_abs, win_lo)
        hi = min(e_abs, win_hi)
        if hi <= lo:
            continue  # no overlap with this row's window
        segments.append(
            {
                "day": day,
                "rel_start_min": lo - win_lo,
                "rel_end_min": hi - win_lo,
                "cont_back": lo > s_abs,   # not the slot's first row → «
                "cont_fwd": hi < e_abs,    # not the slot's last row → »
            }
        )
    return segments


def _groups(camp: Camp) -> list[TimelineGroup]:
    """One row per camp day, carrying its date; the frontend formats weekday + label."""
    return [TimelineGroup(id=i, iso_date=(camp.start_date + timedelta(days=i)).isoformat())
            for i in range(camp.length_days)]


def _categories(camp: Camp) -> list[TimelineCategory]:
    """The camp's categories (user sort_order); segments reference them by cat_key."""
    return [TimelineCategory(id=c.id, key=c.key, label=c.label, color=c.color) for c in camp.categories]


def _orgs(camp: Camp) -> list[TimelineOrg]:
    """All camp orgs (Czech-collated by initials); segments reference them by id."""
    orgs = sorted(camp.orgs, key=lambda o: czech_sort_key(o.initials))
    return [TimelineOrg(id=o.id, initials=o.initials, name=o.name) for o in orgs]


def _tags(camp: Camp) -> list[TimelineTag]:
    """All camp tags (in their sort_order); segments reference them by id (tag_ids)."""
    return [TimelineTag(id=t.id, name=t.name, pinned=t.pinned) for t in camp.tags]


def _segments(camp: Camp) -> list[TimelineSegment]:
    """One segment per day-row each slot touches (a slot crossing the window boundary
    is sliced into several). Activities with no slots contribute none; a NULL category
    becomes cat_key '_none'."""
    window_start = camp.window_start_min
    org_key = {o.id: czech_sort_key(o.initials) for o in camp.orgs}  # org-id lists sort by this
    segments: list[TimelineSegment] = []
    for activity in camp.activities:
        cat_key = activity.category.key if activity.category else "_none"
        garants = sorted((a.org_id for a in activity.assignments if a.role is OrgRole.garant),
                         key=org_key.get)
        helpers = sorted((a.org_id for a in activity.assignments if a.role is OrgRole.helper),
                         key=org_key.get)
        tag_ids = [link.tag_id for link in activity.tags]
        for slot in activity.slots:
            s_abs = _abs_min(slot.start_at, camp.start_date)
            e_abs = _abs_min(slot.end_at, camp.start_date)
            attending = sorted((a.org_id for a in slot.assignments), key=org_key.get)
            for seg in slice_segments(s_abs, e_abs, window_start, camp.length_days):
                segments.append(TimelineSegment(
                    **seg,  # day, rel_start_min, rel_end_min, cont_back, cont_fwd
                    slot_id=slot.id, activity_id=activity.id,
                    role=slot.role, cat_key=cat_key, title=activity.title,
                    override_name=slot.override_name,
                    garants=garants, helpers=helpers, attending=attending,
                    abs_start_min=s_abs, abs_end_min=e_abs, tag_ids=tag_ids,
                ))
    return segments


def build_timeline(camp: Camp) -> dict:
    """Read-only timeline payload: camp params (+ rev), categories, day groups, and
    sliced segments."""
    payload = TimelinePayload(
        camp=TimelineCamp(
            slug=camp.slug, name=camp.name, start_date=camp.start_date.isoformat(),
            length_days=camp.length_days, timezone=camp.timezone,
            window_start_min=camp.window_start_min, snap_minutes=camp.snap_minutes,
            latitude=camp.latitude, longitude=camp.longitude, rev=camp.timeline_rev,
        ),
        categories=_categories(camp),
        orgs=_orgs(camp),
        tags=_tags(camp),
        groups=_groups(camp),
        segments=_segments(camp),
    )
    return payload.model_dump(mode="json")
