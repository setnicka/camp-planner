"""The timeline.

The segment math and payload builder are pure: build_timeline only reads attributes off
the camp graph, so it gets lightweight duck-typed objects (no DB needed), with the real
SlotRole/OrgRole enums the code compares by identity.
"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

from camp_planner.models.activity import OrgRole
from camp_planner.models.slot import SlotRole
from camp_planner.schemas import ConflictOut
from camp_planner.services.timeline import build_timeline, slice_segments
from tests.conftest import (
    ADMIN,
    audit,
    count_queries,
    get_json,
    make_activity,
    make_slot,
    ok,
    viewer,
)


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


# --- timeline ----------------------------------------------------------------

def test_timeline_save_conflict_on_stale_rev(client, seeded):
    url = f"/api/camps/{seeded['slug']}/timeline"
    resp = client.patch(url, json={"rev": 999, "moves": []}, headers=ADMIN)
    assert resp.status_code == 409
    body = resp.get_json()
    assert not body["ok"] and "timeline" in body and body["rev"] == 0
    # The errorhandler's body skips spectree's response validation.
    ConflictOut.model_validate(body)


def test_timeline_batch_create_move_delete(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    url = f"/api/camps/{slug}/timeline"
    assert get_json(client, url)["segments"] == []
    s1 = make_slot(client, slug, aid)
    rev = get_json(client, url)["camp"]["rev"]

    body = ok(client.patch(url, json={
        "rev": rev,
        "creates": [{"activity_id": aid, "role": "prep",
                     "start_at": "2026-07-04T13:30", "end_at": "2026-07-04T14:00"}],
        "moves": [{"slot_id": s1, "start_at": "2026-07-04T15:00", "end_at": "2026-07-04T17:00"}],
    }, headers=ADMIN))
    assert body["rev"] == rev + 1
    assert len(body["created"]) == 1 and body["created"][0]["role"] == "prep"
    tl = get_json(client, url)
    assert len(tl["segments"]) == 2 and tl["camp"]["rev"] == rev + 1

    # one batch-level summary, plus per-slot rows under the activity carrying the diff
    kinds = {(e["entity_type"], e["action"]) for e in audit(client, slug)}
    assert {("timeline", "update"), ("slot", "create"), ("slot", "update")} <= kinds
    slot_rows = audit(client, slug, entity_type="slot")
    assert all(e["activity_id"] == aid for e in slot_rows)
    move = next(e for e in slot_rows if e["action"] == "update")
    assert move["changes"]["start_at"] == ["2026-07-04T14:00:00", "2026-07-04T15:00:00"]

    ok(client.patch(url, json={"rev": body["rev"], "deletes": [s1, body["created"][0]["id"]]},
                    headers=ADMIN))
    assert get_json(client, url)["segments"] == []


def test_timeline_retype_changes_role_and_audits(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    url = f"/api/camps/{slug}/timeline"
    s1 = make_slot(client, slug, aid, role="main")

    def retype(rev):
        body = {"rev": rev, "retypes": [{"slot_id": s1, "role": "cleanup"}]}
        return ok(client.patch(url, json=body, headers=ADMIN))

    def role_diffs():
        return [e["changes"]["role"] for e in audit(client, slug, entity_type="slot")
                if e["action"] == "update" and "role" in e["changes"]]

    rev = get_json(client, url)["camp"]["rev"]
    assert retype(rev)["rev"] == rev + 1
    assert get_json(client, url)["segments"][0]["role"] == "cleanup"
    assert role_diffs() == [["main", "cleanup"]]

    retype(rev + 1)   # the same role again changes nothing and writes no audit row
    assert role_diffs() == [["main", "cleanup"]]


def test_timeline_create_rejects_foreign_activity(client, seeded):
    resp = client.patch(f"/api/camps/{seeded['slug']}/timeline", json={
        "rev": 0,
        "creates": [{"activity_id": 99999, "start_at": "2026-07-04T13:00", "end_at": "2026-07-04T14:00"}],
    }, headers=ADMIN)
    assert resp.status_code == 400


def test_timeline_create_rejects_end_before_start(client, seeded):
    resp = client.patch(f"/api/camps/{seeded['slug']}/timeline", json={
        "rev": 0,
        "creates": [{"activity_id": seeded["activity_id"],
                     "start_at": "2026-07-04T16:00", "end_at": "2026-07-04T14:00"}],
    }, headers=ADMIN)
    assert resp.status_code == 422


def test_timeline_save_requires_rev(client, seeded):
    # rev is the optimistic lock: omitting it must not silently bypass it
    resp = client.patch(f"/api/camps/{seeded['slug']}/timeline", json={"moves": []}, headers=ADMIN)
    assert resp.status_code == 422


def test_timeline_save_force_overrides_stale_rev(client, seeded):
    # the conflict dialog's deliberate "Přepsat"
    resp = client.patch(f"/api/camps/{seeded['slug']}/timeline", json={
        "rev": 999, "force": True,
        "creates": [{"activity_id": seeded["activity_id"],
                     "start_at": "2026-07-04T14:00", "end_at": "2026-07-04T15:00"}],
    }, headers=ADMIN)
    assert resp.status_code == 200


def test_timeline_save_rejects_slot_outside_camp_days(client, seeded):
    """A slot outside the camp window would persist but never render (slice_segments
    clamps it away), an invisible slot no UI could reach. Camp: 2026-07-04 + 3 days,
    window 04:00 → valid range 07-04T04:00 .. 07-07T04:00."""
    slug, aid = seeded["slug"], seeded["activity_id"]
    url = f"/api/camps/{slug}/timeline"

    def save(start, end, slot_id=None):
        rev = get_json(client, url)["camp"]["rev"]
        change = ({"moves": [{"slot_id": slot_id, "start_at": start, "end_at": end}]} if slot_id
                  else {"creates": [{"activity_id": aid, "start_at": start, "end_at": end}]})
        return client.patch(url, json={"rev": rev, **change}, headers=ADMIN)

    resp = save("2026-07-08T10:00", "2026-07-08T12:00")
    assert resp.status_code == 400 and "mimo dny akce" in resp.get_json()["error"]
    assert save("2026-07-04T02:00", "2026-07-04T03:30").status_code == 400

    sid = make_slot(client, slug, aid)
    assert save("2026-07-07T10:00", "2026-07-07T11:00", slot_id=sid).status_code == 400

    # boundaries are inclusive: a night program running to the last window end is fine
    assert save("2026-07-06T22:00", "2026-07-07T04:00").status_code == 200


def test_timeline_span_seconds_are_dropped(client, seeded):
    # sub-minute times would render truncated and never round-trip
    created = ok(client.patch(f"/api/camps/{seeded['slug']}/timeline", json={
        "rev": 0,
        "creates": [{"activity_id": seeded["activity_id"],
                     "start_at": "2026-07-04T14:00:30", "end_at": "2026-07-04T16:00:45"}],
    }, headers=ADMIN))["created"][0]
    assert created["start_at"] == "2026-07-04T14:00:00"
    assert created["end_at"] == "2026-07-04T16:00:00"


def test_timeline_query_count_is_flat(client, seeded):
    """Read and save are eager-loaded (loaders.TIMELINE): their query count must not grow
    with the number of activities (N+1 guard)."""
    slug, org_id = seeded["slug"], seeded["org_id"]
    url = f"/api/camps/{slug}/timeline"

    def wire(activity_id):   # a garant, and a slot with an attendee
        ok(client.put(f"/api/activities/{activity_id}/orgs",
                      json={"orgs": [{"org_id": org_id, "role": "garant"}]}, headers=ADMIN))
        sid = make_slot(client, slug, activity_id)
        ok(client.patch(f"/api/slots/{sid}", json={"org_ids": [org_id]}, headers=ADMIN))
        return sid

    def read_and_save(hour):
        read = count_queries(lambda: ok(client.get(url, headers=ADMIN)))
        body = {"rev": get_json(client, url)["camp"]["rev"], "moves": [
            {"slot_id": moved, "start_at": f"2026-07-04T{hour}:00", "end_at": f"2026-07-04T{hour}:30"}]}
        save = count_queries(lambda: ok(client.patch(url, json=body, headers=ADMIN)))
        return read, save

    moved = wire(seeded["activity_id"])
    wire(make_activity(client, slug, "B"))
    two = read_and_save("12")
    for i in range(3):
        wire(make_activity(client, slug, f"C{i}"))
    assert read_and_save("13") == two


def test_timeline_page_edit_wiring_for_editor(client, seeded):
    slug = seeded["slug"]
    html = client.get(f"/camps/{slug}", headers=ADMIN).get_data(as_text=True)
    assert 'id="cp-edit-toggle"' in html
    assert 'id="cp-timeline-edit"' in html          # the edit-config JSON block
    assert f"/api/camps/{slug}/timeline" in html     # save url
    assert f"/api/camps/{slug}/activities" in html   # picker url
    assert 'name="csrf-token"' in html
    assert 'class="cp-help-edit"' in html


def test_timeline_page_read_only_for_viewer(client, seeded):
    slug = seeded["slug"]
    html = client.get(f"/camps/{slug}", headers=viewer(slug)).get_data(as_text=True)
    assert 'id="cp-edit-toggle"' not in html
    assert 'id="cp-timeline-edit"' not in html
    # viewers still get the way into the activity detail (slot select → Detail button)
    assert f'data-activity-detail="/camps/{slug}/activities/0"' in html
    assert 'class="cp-help-view"' in html
    assert 'class="cp-help-edit"' not in html
