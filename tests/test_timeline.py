"""Unit tests for the timeline segment math and payload builder.

build_timeline only reads attributes off the camp graph, so we feed it lightweight
duck-typed objects — no DB needed. Real SlotRole/OrgRole enums are used since the
code compares against them by identity.
"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

from camp_planner.models.activity import OrgRole
from camp_planner.models.slot import SlotRole
from camp_planner.services.timeline import build_timeline, slice_segments

WINDOW = 4 * 60  # 04:00


def test_single_slot_within_one_day():
    # 14:30–18:00 on day 0 -> one segment, no continuation.
    segs = slice_segments(14 * 60 + 30, 18 * 60, WINDOW, length_days=3)
    assert len(segs) == 1
    s = segs[0]
    assert s["day"] == 0
    assert (s["rel_start_min"], s["rel_end_min"]) == (14 * 60 + 30 - WINDOW, 18 * 60 - WINDOW)
    assert not s["cont_back"] and not s["cont_fwd"]


def test_night_slot_crossing_midnight_stays_on_one_row():
    # 22:00–02:30 sits inside the 04:00->04:00 window of day 0.
    segs = slice_segments(22 * 60, 26 * 60 + 30, WINDOW, length_days=3)
    assert len(segs) == 1
    assert segs[0]["day"] == 0


def test_multi_day_slot_is_sliced_with_continuation_flags():
    # 18:00 day0 -> 08:00 day1 spans the 04:00 boundary -> two segments.
    segs = slice_segments(18 * 60, 24 * 60 + 8 * 60, WINDOW, length_days=3)
    assert [s["day"] for s in segs] == [0, 1]
    assert segs[0]["cont_fwd"] and not segs[0]["cont_back"]
    assert segs[1]["cont_back"] and not segs[1]["cont_fwd"]
    assert segs[0]["rel_end_min"] == 24 * 60  # fills to the window end
    assert segs[1]["rel_start_min"] == 0      # resumes at the window start


def test_exact_window_boundary_does_not_spawn_empty_next_row():
    # ending exactly at 04:00 next day must NOT create a zero-width day-1 segment.
    segs = slice_segments(18 * 60, 24 * 60 + WINDOW, WINDOW, length_days=3)
    assert [s["day"] for s in segs] == [0]
    assert not segs[0]["cont_fwd"]


def test_slot_before_the_first_window_is_dropped():
    # 02:00–03:00 on day 0 falls before the window opens -> belongs to no row.
    assert slice_segments(2 * 60, 3 * 60, WINDOW, length_days=3) == []


def test_slot_past_last_day_is_clamped_out():
    # entirely beyond the last camp day -> no segment.
    assert slice_segments(10 * 24 * 60, 10 * 24 * 60 + 60, WINDOW, length_days=2) == []


# --- build_timeline -----------------------------------------------------------

def _slot(start: datetime, end: datetime, role=SlotRole.main, assignments=()):
    return SimpleNamespace(id=1, start_at=start, end_at=end, role=role, override_name=None,
                           assignments=list(assignments))


def _camp(activities, *, length_days=3, orgs=(), tags=()):
    return SimpleNamespace(
        slug="c", name="C", start_date=date(2026, 7, 4), length_days=length_days,
        timezone="Europe/Prague", window_start_min=WINDOW, snap_minutes=15, timeline_rev=7,
        latitude=None, longitude=None,
        categories=[SimpleNamespace(id=1, key="hra-fyzicka", label="Fyzická hra", color="#0b8043")],
        orgs=list(orgs), tags=list(tags),
        activities=activities,
    )


def _activity(*, slots=(), category=None, assignments=(), tags=()):
    return SimpleNamespace(
        id=1, title="A", category=category, slots=list(slots),
        assignments=list(assignments), tags=list(tags),
    )


def test_build_timeline_groups_carry_iso_date():
    payload = build_timeline(_camp([]))
    assert payload["groups"][0] == {"id": 0, "iso_date": "2026-07-04"}
    assert payload["camp"]["rev"] == 7


def test_build_timeline_activity_with_no_slots_yields_no_segments():
    payload = build_timeline(_camp([_activity()]))
    assert payload["segments"] == []


def test_build_timeline_null_category_maps_to_none_key():
    act = _activity(slots=[_slot(datetime(2026, 7, 4, 14), datetime(2026, 7, 4, 16))])
    payload = build_timeline(_camp([act]))
    assert payload["segments"][0]["cat_key"] == "_none"


def test_build_timeline_segment_references_orgs_by_id():
    orgs = [SimpleNamespace(id=1, initials="ÁL", name="Alena"),
            SimpleNamespace(id=2, initials="MK", name="Marek"),
            SimpleNamespace(id=3, initials="JN", name="Jana")]
    act = _activity(
        slots=[_slot(datetime(2026, 7, 4, 14), datetime(2026, 7, 4, 16),
                     assignments=[SimpleNamespace(org_id=3)])],
        category=SimpleNamespace(key="hra-fyzicka"),
        assignments=[SimpleNamespace(org_id=1, role=OrgRole.garant),
                     SimpleNamespace(org_id=2, role=OrgRole.helper)],
    )
    payload = build_timeline(_camp([act], orgs=orgs))
    seg = payload["segments"][0]
    assert seg["cat_key"] == "hra-fyzicka"
    assert (seg["garants"], seg["helpers"], seg["attending"]) == ([1], [2], [3])
    assert payload["orgs"][0] == {"id": 1, "initials": "ÁL", "name": "Alena"}


def test_build_timeline_segment_org_ids_sorted_czech():
    # 'Á' must sort next to 'A' (before 'M'), not after 'Z' as code-point order would.
    orgs = [SimpleNamespace(id=1, initials="M", name="M"),
            SimpleNamespace(id=2, initials="Á", name="Á")]
    act = _activity(
        slots=[_slot(datetime(2026, 7, 4, 14), datetime(2026, 7, 4, 16))],
        assignments=[SimpleNamespace(org_id=1, role=OrgRole.garant),   # M, given first
                     SimpleNamespace(org_id=2, role=OrgRole.garant)],  # Á, given second
    )
    payload = build_timeline(_camp([act], orgs=orgs))
    assert payload["segments"][0]["garants"] == [2, 1]          # Á before M in a segment
    assert [o["initials"] for o in payload["orgs"]] == ["Á", "M"]  # and in payload.orgs


def test_build_timeline_tags_lookup():
    tags = [SimpleNamespace(id=5, name="Hotovo", pinned=True),
            SimpleNamespace(id=8, name="Riziko", pinned=False)]
    payload = build_timeline(_camp([], tags=tags))
    assert payload["tags"] == [{"id": 5, "name": "Hotovo", "pinned": True},
                               {"id": 8, "name": "Riziko", "pinned": False}]
