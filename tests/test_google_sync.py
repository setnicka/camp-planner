"""Google Calendar sync: service + API tests.

The Google boundary is faked at the googleapiclient *service* level (over an in-memory
event store), so the real batch_push, list_events and verify_access run in every test.
event_body / parse_event_times are pure and run for real too.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date, datetime

import pytest
from flask import g
from googleapiclient.errors import HttpError

from camp_planner.auth.identity import build_identity
from camp_planner.extensions import db
from camp_planner.models.activity import Activity, ActivityAssignment, OrgRole
from camp_planner.models.audit import AuditLog, EntityType
from camp_planner.models.camp import Camp, Category
from camp_planner.models.google import GoogleSyncOp, SyncOpKind
from camp_planner.models.slot import Slot, SlotAssignment, SlotRole
from camp_planner.schemas import ActivityOrgsIn, ActivityUpdate, GooglePullConflictOut
from camp_planner.services import activities, errors, google_client, google_sync
from camp_planner.services import camps as camps_service
from camp_planner.services.timeline import bump_timeline_rev
from tests.conftest import ADMIN, add_org, editor, ok, viewer

CAL = "cal@group.calendar.google.com"
SLUG = "t"
SLOT_START, SLOT_END = datetime(2026, 7, 4, 14), datetime(2026, 7, 4, 16)


def _http_error(status):
    resp = type("Resp", (), {"status": status, "reason": "x"})()
    return HttpError(resp, b"{}")


class FakeGoogle:
    """An in-memory calendar, with per-test failure knobs:
    fail_next (next op raises), fail_batch (next whole batch HTTP call dies),
    gone (event ids whose PATCH 400s: deleted/cancelled upstream),
    error_ids (event ids whose ops 500), no_insert_id (insert response lacks the id)."""

    def __init__(self):
        self.events: dict[str, dict] = {}
        self._n = 0
        self.calls = {"insert": 0, "patch": 0, "delete": 0}
        self.batch_count = 0
        self.fail_next = False
        self.fail_batch = False
        self.gone: set[str] = set()
        self.error_ids: set[str] = set()
        self.no_insert_id = False
        self.page_size: int | None = None  # when set, list() paginates in chunks this big
        self.list_calls = 0

    def _maybe_fail(self, event_id):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("boom")
        if event_id in self.error_ids:
            raise _http_error(500)

    def insert(self, calendar_id, body):
        self._maybe_fail(None)
        self.calls["insert"] += 1
        self._n += 1
        eid = f"evt{self._n}"
        self.events[eid] = {**body, "id": eid}
        return {} if self.no_insert_id else {"id": eid}

    def patch(self, calendar_id, event_id, body):
        self._maybe_fail(event_id)
        if event_id in self.gone:  # cancelled recurring instance / deleted event → PATCH 400s
            raise _http_error(400)
        self.calls["patch"] += 1
        self.events[event_id] = {**self.events.get(event_id, {}), **body, "id": event_id}
        return {"id": event_id}

    def delete(self, calendar_id, event_id):
        self._maybe_fail(event_id)
        if event_id not in self.events:
            raise _http_error(404)  # like Google: deleting an already-gone event 404s
        self.calls["delete"] += 1
        del self.events[event_id]
        return {}

    def add_external(self, eid, summary, start, end):
        """A user-created event (no cpSlotId marker), as if added directly in Google."""
        self.events[eid] = {
            "id": eid, "summary": summary, "status": "confirmed",
            "start": {"dateTime": start, "timeZone": "Europe/Prague"},
            "end": {"dateTime": end, "timeZone": "Europe/Prague"},
        }
        return self.events[eid]


# The googleapiclient surface the client touches: events() request builders (deferred)
# and batches that fire the callback per op.

class _Req:
    def __init__(self, fn):
        self.execute = fn


class _FakeEvents:
    def __init__(self, g):
        self._g = g

    def insert(self, calendarId, body):
        return _Req(lambda: self._g.insert(calendarId, body))

    def patch(self, calendarId, eventId, body):
        return _Req(lambda: self._g.patch(calendarId, eventId, body))

    def delete(self, calendarId, eventId):
        return _Req(lambda: self._g.delete(calendarId, eventId))

    def list(self, **params):
        return _Req(lambda: self._page(params))

    def _page(self, params):
        """One page of the event listing: everything at once, or with page_size set, a
        chunk plus a nextPageToken until the last."""
        self._g.list_calls += 1
        items = list(self._g.events.values())
        size = self._g.page_size
        if not size:
            return {"items": items}
        offset = int(params.get("pageToken", 0))
        chunk = items[offset:offset + size]
        resp: dict = {"items": chunk}
        if offset + size < len(items):
            resp["nextPageToken"] = str(offset + size)
        return resp


class _FakeBatch:
    def __init__(self, g, callback):
        self._g = g
        self._callback = callback
        self._reqs = []

    def add(self, request, request_id):
        self._reqs.append((request_id, request))

    def execute(self):
        if self._g.fail_batch:
            self._g.fail_batch = False
            raise RuntimeError("network down")
        for rid, req in self._reqs:
            try:
                self._callback(rid, req.execute(), None)
            except Exception as exc:  # noqa: BLE001 (the real batch reports errors via the callback)
                self._callback(rid, None, exc)


class _FakeService:
    def __init__(self, g):
        self._g = g

    def events(self):
        return _FakeEvents(self._g)

    def new_batch_http_request(self, callback):
        self._g.batch_count += 1
        return _FakeBatch(self._g, callback)


@pytest.fixture(autouse=True)
def _identity(app):
    """Direct service calls still reach audit.record, which reads g.identity. Client
    requests build their own from the X-Remote-* headers, so this doesn't leak into them."""
    g.identity = build_identity(user_id="tester", is_admin=True)


@pytest.fixture
def gcal(app, monkeypatch):
    """Enable the feature and point google_client's HTTP client at the in-memory fake;
    everything above it (batch_push, list_events, verify_access) runs for real."""
    fake = FakeGoogle()
    monkeypatch.setattr(google_client, "is_configured", lambda: True)
    monkeypatch.setattr(google_client, "service_account_email", lambda: "sa@test.iam")
    monkeypatch.setattr(google_client, "client", lambda: _FakeService(fake))
    return fake


def _camp(seeded) -> Camp:
    return db.session.get(Camp, seeded["camp_id"])


def _connect(camp):
    camp.google_calendar_id = CAL
    db.session.commit()


def _slot(activity_id, start=SLOT_START, end=SLOT_END, role=SlotRole.main) -> Slot:
    slot = Slot(activity_id=activity_id, start_at=start, end_at=end, role=role)
    db.session.add(slot)
    db.session.commit()
    return slot


def _new_camp(slug, start, length=3, window=240):
    camp = Camp(name=slug.upper(), slug=slug, start_date=start, length_days=length,
                window_start_min=window, snap_minutes=15)
    db.session.add(camp)
    db.session.commit()
    return camp


@pytest.fixture
def queued(seeded):
    """The seeded camp, connected, with one slot whose upsert waits in the queue."""
    camp = _camp(seeded)
    _connect(camp)
    slot = _slot(seeded["activity_id"])
    google_sync.enqueue_upsert(camp, slot)
    db.session.commit()
    return camp, slot


@pytest.fixture
def synced(queued, gcal):
    """...and drained, so the slot has its Google event."""
    camp, slot = queued
    google_sync.drain(camp)
    return camp, slot


def _preview(client):
    return ok(client.get(f"/api/camps/{SLUG}/google/pull", headers=editor(SLUG)))


def _apply(client, decisions, **extra):
    return client.post(f"/api/camps/{SLUG}/google/pull",
                       json={"decisions": decisions, **extra}, headers=editor(SLUG))


def _change(preview, kind):
    return next(c for c in preview["changes"] if c["kind"] == kind)


def _move_event(gcal, slot, start, end):
    event = gcal.events[slot.google_event_id]
    event["start"]["dateTime"], event["end"]["dateTime"] = start, end


# --- pure payload / timezone ---------------------------------------------------------

def test_event_body_uses_camp_timezone(app, seeded):
    slot = _slot(seeded["activity_id"])
    body = google_client.event_body(slot)
    assert body["start"] == {"dateTime": "2026-07-04T14:00:00", "timeZone": "Europe/Prague"}
    assert body["end"]["dateTime"] == "2026-07-04T16:00:00"
    assert body["summary"] == "Akce"
    assert body["extendedProperties"]["private"]["cpSlotId"] == str(slot.id)


def test_prep_slot_summary_suffix(app, seeded):
    slot = _slot(seeded["activity_id"], role=SlotRole.prep)
    assert google_client.event_body(slot)["summary"] == "Akce (příprava)"


@pytest.mark.parametrize("start, end, expected", [
    # 14:00 in Prague is +02:00 in July; parsed back to the naive local 14:00
    ({"dateTime": "2026-07-04T14:00:00+02:00"}, {"dateTime": "2026-07-04T16:00:00+02:00"},
     (datetime(2026, 7, 4, 14), datetime(2026, 7, 4, 16))),
    # an offset-less dateTime with its own timeZone: 09:00 New York (EDT) is 15:00 Prague
    ({"dateTime": "2026-07-04T09:00:00", "timeZone": "America/New_York"},
     {"dateTime": "2026-07-04T11:00:00", "timeZone": "America/New_York"},
     (datetime(2026, 7, 4, 15), datetime(2026, 7, 4, 17))),
    # neither: already camp-local wall-clock
    ({"dateTime": "2026-07-04T14:00:00"}, {"dateTime": "2026-07-04T16:00:00"},
     (datetime(2026, 7, 4, 14), datetime(2026, 7, 4, 16))),
    ({"date": "2026-07-04"}, {"date": "2026-07-05"}, None),   # all-day
])
def test_parse_event_times(start, end, expected):
    assert google_client.parse_event_times({"start": start, "end": end}, "Europe/Prague") == expected


# --- inbound field parsing (pure; the apply paths are covered by the e2e tests below) --

@pytest.fixture
def roster_camp(app, seeded):
    """The seeded camp (org "K") with a few more orgs for the parsing matrices."""
    camp = _camp(seeded)
    for ini, name in [("M", "Marek"), ("P", "Petr"), ("H", "Hugo"),
                      ("Á", "Ája"), ("B", "Bob"), ("L", "Lola")]:
        add_org(camp.id, ini, name)
    db.session.commit()
    return camp


def _names(camp, org_ids):
    by_id = {o.id: o.initials for o in camp.orgs}
    return [by_id[i] for i in org_ids]


@pytest.mark.parametrize(("text", "matched", "unknown"), [
    ("K", ["K"], []),
    ("K, M", ["K", "M"], []),
    ("K + M", ["K", "M"], []),           # plus separates like a comma
    ("k;m", ["K", "M"], []),             # case-insensitive; semicolons too
    ("(K) M", ["K", "M"], []),           # parentheses are ignored
    ("K, ZZ", ["K"], ["ZZ"]),            # unmatched token → unknown
    ("K K", ["K"], []),                  # duplicates collapse
    (None, [], []),
])
def test_match_initials_matrix(roster_camp, text, matched, unknown):
    ids, unk = google_sync._match_initials(roster_camp, text)
    assert _names(roster_camp, ids) == matched
    assert unk == unknown


@pytest.mark.parametrize(("text", "garants", "helpers", "unknown"), [
    ("K", ["K"], [], []),
    ("K+M, P", ["K", "M"], ["P"], []),               # '+' joins garants; later items = helpers
    ("H (Á, B, L)", ["H"], ["Á", "B", "L"], []),     # parens act as commas
    ("(K M), (P)", ["K", "M"], ["P"], []),           # spaces separate within an item
    ("K, ZZ", ["K"], [], ["ZZ"]),
    (None, [], [], []),
])
def test_parse_location_matrix(roster_camp, text, garants, helpers, unknown):
    garant_ids, helper_ids, unk = google_sync._parse_location(roster_camp, text)
    assert _names(roster_camp, garant_ids) == garants
    assert _names(roster_camp, helper_ids) == helpers
    assert unk == unknown


def test_color_to_category_snaps_to_nearest(app, seeded):
    camp = _camp(seeded)                                 # seeded category is #0b8043 (Basil)
    red = Category(camp_id=camp.id, key="vystraha", label="Výstraha",
                   color="#d50000", sort_order=1)
    db.session.add(red)
    db.session.commit()

    assert google_sync._color_to_category(camp, "10") == seeded["cat_id"]  # Basil → green
    assert google_sync._color_to_category(camp, "11") == red.id            # Tomato → red
    assert google_sync._color_to_category(camp, None) is None
    # and outbound: the seeded category's colour is an exact palette match
    assert google_client.event_color_id(db.session.get(Activity, seeded["activity_id"])) == "10"


# --- batch_push (chunking + outcome mapping over the fake service) --------------------

def test_batch_push_chunks_and_maps_outcomes(app, gcal):
    Op = google_client.PushOp
    gcal.error_ids.add("boom")

    ops = [Op(key=f"i{i}", kind="insert", calendar_id=CAL, body={"summary": str(i)}) for i in range(120)]
    ops += [Op(key="p", kind="patch", calendar_id=CAL, event_id="evtP", body={"summary": "P"}),
            Op(key="dgone", kind="delete", calendar_id=CAL, event_id="not-there"),
            Op(key="dboom", kind="delete", calendar_id=CAL, event_id="boom")]

    results = google_client.batch_push(ops)

    assert gcal.batch_count == 5                      # 123 ops at ≤25/batch
    assert results["i0"].ok and results["i0"].event_id == "evt1"
    assert results["p"].ok
    assert results["dgone"].ok and results["dgone"].event_id is None  # 404 → already gone → success
    assert not results["dboom"].ok and results["dboom"].error        # 500 → genuine failure
    assert len(results) == len(ops)


def test_list_events_paginates_across_pages(app, gcal):
    for i in range(3):
        gcal.add_external(f"ext{i}", f"E{i}", "2030-01-01T10:00:00", "2030-01-01T11:00:00")
    gcal.page_size = 1

    events = google_client.list_events(CAL)

    assert [e["id"] for e in events] == ["ext0", "ext1", "ext2"]
    assert gcal.list_calls == 3


# --- connect / disconnect ------------------------------------------------------------

def test_connect_queues_full_export_then_drain(app, seeded, gcal):
    camp = _camp(seeded)
    _slot(seeded["activity_id"])
    _slot(seeded["activity_id"], datetime(2026, 7, 5, 14), datetime(2026, 7, 5, 16))

    camps_service.set_google_calendar(camp, CAL)
    assert camp.google_calendar_id == CAL
    assert google_sync.pending_count(camp) == 2  # one upsert per existing slot

    result = google_sync.drain(camp)
    assert result == {"pushed": 2, "failed": 0, "pending": 0}
    assert len(gcal.events) == 2
    assert all(s.google_event_id for s in db.session.scalars(db.select(Slot)).all())


def test_disconnect_forgets_mapping_and_queue(app, seeded, gcal):
    camp = _camp(seeded)
    slot = _slot(seeded["activity_id"])
    camps_service.set_google_calendar(camp, CAL)
    google_sync.drain(camp)
    assert slot.google_event_id

    camps_service.disconnect_google(camp)
    assert camp.google_calendar_id is None
    assert db.session.get(Slot, slot.id).google_event_id is None
    assert google_sync.pending_count(camp) == 0


def test_resync_all_queues_every_slot(app, seeded, gcal):
    camp = _camp(seeded)
    _connect(camp)
    _slot(seeded["activity_id"])
    _slot(seeded["activity_id"], datetime(2026, 7, 5, 14), datetime(2026, 7, 5, 16))

    assert google_sync.resync_all(camp) == {"queued": 2}
    assert google_sync.pending_count(camp) == 2
    assert google_sync.resync_all(camp) == {"queued": 2}  # idempotent, dedupes against queued upserts
    assert google_sync.pending_count(camp) == 2

    google_sync.drain(camp)
    assert len(gcal.events) == 2


def test_nothing_is_queued_while_not_connected(app, seeded):
    camp = _camp(seeded)
    google_sync.enqueue_upsert(camp, _slot(seeded["activity_id"]))
    db.session.commit()
    assert google_sync.resync_all(camp) == {"queued": 0}
    assert db.session.scalar(db.select(db.func.count()).select_from(GoogleSyncOp)) == 0


def test_drain_recreates_event_gone_upstream(synced, gcal):
    """A slot whose event vanished upstream (deleted, or a cancelled recurring instance
    whose id PATCH-400s forever) is re-created as a fresh event, not retried forever."""
    camp, slot = synced
    old_id = slot.google_event_id

    gcal.gone.add(old_id)
    google_sync.enqueue_upsert(camp, slot)
    db.session.commit()

    result = google_sync.drain(camp)
    db.session.refresh(slot)
    assert slot.google_event_id and slot.google_event_id != old_id
    assert result == {"pushed": 1, "failed": 0, "pending": 0}
    assert slot.google_event_id in gcal.events


def test_connect_rejects_time_overlap_on_shared_calendar(app, seeded, gcal):
    camps_service.set_google_calendar(_camp(seeded), CAL)   # 2026-07-04 .. 07-07

    overlapping = _new_camp("b", date(2026, 7, 6))
    with pytest.raises(errors.Invalid):
        camps_service.set_google_calendar(overlapping, CAL)
    assert overlapping.google_calendar_id is None

    free = _new_camp("c", date(2026, 7, 20))
    camps_service.set_google_calendar(free, CAL)
    assert free.google_calendar_id == CAL


def test_change_days_rejected_when_overlap_on_shared_calendar(app, seeded, gcal):
    camps_service.set_google_calendar(_camp(seeded), CAL)   # 07-04 .. 07-07
    camp_b = _new_camp("b", date(2026, 7, 20))
    camps_service.set_google_calendar(camp_b, CAL)

    with pytest.raises(errors.Invalid):
        camps_service.save_camp_settings(camp_b, {"start_date": date(2026, 7, 5)}, allow_meta=False)
    db.session.expire_all()
    assert db.session.get(Camp, camp_b.id).start_date == date(2026, 7, 20)

    camps_service.save_camp_settings(camp_b, {"start_date": date(2026, 8, 1)}, allow_meta=False)
    db.session.expire_all()
    assert db.session.get(Camp, camp_b.id).start_date == date(2026, 8, 1)


def test_reconnect_adopts_existing_events(app, seeded, gcal):
    camp = _camp(seeded)
    slot = _slot(seeded["activity_id"])
    camps_service.set_google_calendar(camp, CAL)
    google_sync.drain(camp)
    assert gcal.calls["insert"] == 1 and len(gcal.events) == 1
    event_id = db.session.get(Slot, slot.id).google_event_id

    camps_service.disconnect_google(camp)                  # leaves the event in Google
    assert db.session.get(Slot, slot.id).google_event_id is None
    assert len(gcal.events) == 1

    camps_service.set_google_calendar(camp, CAL)           # the SAME calendar again
    assert db.session.get(Slot, slot.id).google_event_id == event_id  # adopted by cpSlotId
    google_sync.drain(camp)
    assert gcal.calls["insert"] == 1
    assert gcal.calls["patch"] >= 1 and len(gcal.events) == 1


# --- timeline edits flow through to Google -------------------------------------------

def test_timeline_create_move_delete(client, seeded, gcal):
    camp = _camp(seeded)
    _connect(camp)
    url, hdr = f"/api/camps/{SLUG}/timeline", editor(SLUG)

    body = {"rev": camp.timeline_rev, "creates": [
        {"activity_id": seeded["activity_id"], "role": "main",
         "start_at": "2026-07-04T14:00:00", "end_at": "2026-07-04T16:00:00"}]}
    assert client.patch(url, json=body, headers=hdr).status_code == 200
    google_sync.drain(camp)
    assert len(gcal.events) == 1
    slot = db.session.scalar(db.select(Slot))
    event_id = slot.google_event_id
    assert event_id and gcal.events[event_id]["start"]["dateTime"] == "2026-07-04T14:00:00"

    body = {"rev": camp.timeline_rev, "moves": [
        {"slot_id": slot.id, "start_at": "2026-07-04T15:00:00", "end_at": "2026-07-04T17:00:00"}]}
    assert client.patch(url, json=body, headers=hdr).status_code == 200
    google_sync.drain(camp)
    assert len(gcal.events) == 1      # patched in place
    assert gcal.events[event_id]["start"]["dateTime"] == "2026-07-04T15:00:00"

    body = {"rev": camp.timeline_rev, "deletes": [slot.id]}
    assert client.patch(url, json=body, headers=hdr).status_code == 200
    google_sync.drain(camp)
    assert gcal.events == {}


def test_activity_rename_repushes_slot_events(seeded, synced, gcal):
    camp, slot = synced
    activity = db.session.get(Activity, seeded["activity_id"])
    activities.update_activity(activity, ActivityUpdate(title="Nová akce"))
    assert google_sync.pending_count(camp) == 1
    google_sync.drain(camp)
    assert gcal.events[slot.google_event_id]["summary"] == "Nová akce"


def test_enqueue_dedupes_ops_per_slot(app, seeded, gcal):
    camp = _camp(seeded)
    _connect(camp)
    slot = _slot(seeded["activity_id"])
    for _ in range(3):
        google_sync.enqueue_upsert(camp, slot)
    db.session.commit()
    assert google_sync.pending_count(camp) == 1  # deduped at insert

    result = google_sync.drain(camp)
    assert result == {"pushed": 1, "failed": 0, "pending": 0}
    assert gcal.calls == {"insert": 1, "patch": 0, "delete": 0}
    assert len(gcal.events) == 1


def test_drain_dedupes_raced_duplicates(app, seeded, gcal):
    """drain() is the safety net for duplicate rows a concurrent request could race in,
    which the insert-time check can't see."""
    camp = _camp(seeded)
    _connect(camp)
    slot = _slot(seeded["activity_id"])
    db.session.add_all([GoogleSyncOp(camp_id=camp.id, slot_id=slot.id, op=SyncOpKind.upsert),
                        GoogleSyncOp(camp_id=camp.id, slot_id=slot.id, op=SyncOpKind.upsert)])
    db.session.commit()

    result = google_sync.drain(camp)
    assert result == {"pushed": 1, "failed": 0, "pending": 0}
    assert gcal.calls["insert"] == 1


def test_helper_only_change_repushes(seeded, synced):
    camp, _ = synced
    helper = add_org(camp.id, "M", "Marek")
    db.session.commit()
    assert google_sync.pending_count(camp) == 0

    # LOCATION carries helpers too, so a helper-only change must re-push
    activity = db.session.get(Activity, seeded["activity_id"])
    activities.set_orgs(activity, ActivityOrgsIn(orgs=[{"org_id": helper.id, "role": OrgRole.helper}]))
    assert google_sync.pending_count(camp) == 1


def test_a_failed_push_is_logged_kept_and_retried(client, queued, gcal, caplog):
    camp, _ = queued
    gcal.fail_next = True
    with caplog.at_level(logging.INFO, logger="camp_planner.services.google_sync"):
        result = google_sync.drain(camp)
        assert result["failed"] == 1 and result["pending"] == 1
        op = db.session.scalar(db.select(GoogleSyncOp))
        assert op.attempts == 1 and op.last_error

        status = ok(client.get(f"/api/camps/{SLUG}/google", headers=editor(SLUG)))["google"]
        assert status["failed_ops"] == 1 and status["last_error"]

        result = google_sync.drain(camp)
    assert result["pushed"] == 1 and google_sync.pending_count(camp) == 0
    assert "Google Calendar push failed" in caplog.text
    assert "Google Calendar: created event" in caplog.text


def test_drain_whole_batch_failure_fails_all_ops(app, seeded, gcal):
    """A failure of the whole batch HTTP call fails every op in it; they stay queued with
    the error recorded and the next drain delivers them."""
    camp = _camp(seeded)
    _connect(camp)
    for day in (4, 5):
        slot = _slot(seeded["activity_id"], datetime(2026, 7, day, 14), datetime(2026, 7, day, 16))
        google_sync.enqueue_upsert(camp, slot)
    db.session.commit()

    gcal.fail_batch = True
    result = google_sync.drain(camp)
    assert result == {"pushed": 0, "failed": 2, "pending": 2}
    assert all(op.attempts == 1 and op.last_error
               for op in db.session.scalars(db.select(GoogleSyncOp)))

    result = google_sync.drain(camp)
    assert result == {"pushed": 2, "failed": 0, "pending": 0}


def test_drain_insert_without_id_is_kept_for_retry(queued, gcal):
    """Dropping the op would leave the slot unmapped forever."""
    camp, slot = queued
    gcal.no_insert_id = True
    result = google_sync.drain(camp)
    assert result == {"pushed": 0, "failed": 1, "pending": 1}
    assert db.session.get(Slot, slot.id).google_event_id is None
    assert "nevrátil id" in db.session.scalar(db.select(GoogleSyncOp)).last_error

    gcal.no_insert_id = False
    result = google_sync.drain(camp)
    assert result["pushed"] == 1 and db.session.get(Slot, slot.id).google_event_id


# --- API permissions / feature gating ------------------------------------------------

def test_status_endpoint_requires_edit(client, seeded, gcal):
    assert client.get(f"/api/camps/{SLUG}/google", headers=viewer(SLUG)).status_code == 403
    resp = client.get(f"/api/camps/{SLUG}/google", headers=editor(SLUG))
    assert ok(resp)["google"]["enabled"] is True


def test_connect_and_sync_via_api(client, seeded, gcal):
    _slot(seeded["activity_id"])
    resp = client.put(f"/api/camps/{SLUG}/google", json={"calendar_id": CAL}, headers=editor(SLUG))
    assert ok(resp)["google"]["connected"] is True

    resp = client.post(f"/api/camps/{SLUG}/google/sync", headers=editor(SLUG))
    assert ok(resp)["result"]["pushed"] == 1 and resp.get_json()["result"]["failed"] == 0


def test_resync_via_api(client, seeded, gcal):
    _connect(_camp(seeded))
    _slot(seeded["activity_id"])

    assert client.post(f"/api/camps/{SLUG}/google/resync", headers=viewer(SLUG)).status_code == 403
    body = ok(client.post(f"/api/camps/{SLUG}/google/resync", headers=editor(SLUG)))
    assert body["result"]["queued"] == 1 and body["google"]["pending_ops"] == 1


def test_feature_disabled_rejects_connect(client, seeded):
    # no gcal fixture → is_configured() is False (no GOOGLE_SERVICE_ACCOUNT_JSON)
    resp = client.put(f"/api/camps/{SLUG}/google", json={"calendar_id": CAL}, headers=ADMIN)
    assert resp.status_code == 400
    assert "nastaven" in resp.get_json()["error"]


# --- inbound (Google → Planner) reviewed import --------------------------------------

def test_preview_classifies_changes(client, synced, gcal):
    camp, slot = synced
    _move_event(gcal, slot, "2026-07-04T15:00:00", "2026-07-04T17:00:00")
    gcal.add_external("ext1", "Táborák", "2026-07-06T20:00:00", "2026-07-06T22:00:00")

    kinds = {c["kind"]: c for c in _preview(client)["changes"]}
    assert set(kinds) == {"time_change", "new_event"}
    assert kinds["new_event"]["summary"] == "Táborák"
    assert kinds["time_change"]["new_start"].endswith("15:00:00")


def test_the_import_window(client, synced, gcal):
    """The camp window is [2026-07-04 04:00, 2026-07-07 04:00], its end inclusive; an
    event longer than 48 h is no activity even inside it."""
    camp, slot = synced
    _move_event(gcal, slot, "2026-07-06T20:00:00", "2026-07-08T02:00:00")   # past the end
    gcal.add_external("before", "Před táborem", "2026-07-01T10:00:00", "2026-07-01T12:00:00")
    gcal.add_external("during", "Během", "2026-07-05T10:00:00", "2026-07-05T12:00:00")
    gcal.add_external("long", "Celý tábor", "2026-07-04T08:00:00", "2026-07-07T02:00:00")
    gcal.add_external("over", "Přesčas", "2026-07-06T22:00:00", "2026-07-08T03:00:00")
    gcal.add_external("night", "Noční", "2026-07-06T22:00:00", "2026-07-07T04:00:00")

    changes = _preview(client)["changes"]
    assert {c["summary"] for c in changes if c["kind"] == "new_event"} == {"Během", "Noční"}
    assert not any(c["kind"] == "time_change" for c in changes)


def test_apply_time_change_and_import_new(client, seeded, synced, gcal):
    camp, slot = synced
    _move_event(gcal, slot, "2026-07-04T15:00:00", "2026-07-04T17:00:00")
    gcal.add_external("ext1", "Táborák", "2026-07-06T20:00:00", "2026-07-06T22:00:00")

    resp = _apply(client, [{"key": f"time:{slot.id}", "action": "apply"},
                           {"key": "new:ext1", "action": "new", "category_id": seeded["cat_id"]}])
    assert ok(resp)["applied"] == {
        "created_activities": 1, "imported_slots": 1, "updated": 1, "deleted": 0}

    db.session.expire_all()
    assert db.session.get(Slot, slot.id).start_at == datetime(2026, 7, 4, 15)
    imported = db.session.scalar(db.select(Slot).where(Slot.google_event_id == "ext1"))
    assert imported is not None and imported.activity.title == "Táborák"
    # importing queued an upsert, so the next drain stamps the cpSlotId marker on ext1
    google_sync.drain(_camp(seeded))
    assert gcal.events["ext1"]["extendedProperties"]["private"]["cpSlotId"] == str(imported.id)


def test_apply_pull_reports_vanished_changes_as_skipped(client, synced, gcal):
    """A chosen change gone by apply time (the event changed again in Google meanwhile)
    is reported in `skipped`, not silently dropped."""
    camp, slot = synced
    _move_event(gcal, slot, "2026-07-04T15:00:00", "2026-07-04T17:00:00")
    preview = _preview(client)
    key = _change(preview, "time_change")["key"]

    _move_event(gcal, slot, "2026-07-04T14:00:00", "2026-07-04T16:00:00")   # back to the slot's times

    out = ok(_apply(client, [{"key": key, "action": "apply"}], rev=preview["rev"]))
    assert out["skipped"] == [key]
    assert out["applied"]["updated"] == 0
    assert out["google"]["last_pull_at"]  # the apply response carries the fresh status


def test_apply_pull_stale_rev_conflicts(client, seeded, synced, gcal):
    camp, slot = synced
    _move_event(gcal, slot, "2026-07-04T15:00:00", "2026-07-04T17:00:00")
    preview = _preview(client)

    bump_timeline_rev(_camp(seeded))  # a concurrent timeline edit moves the lock
    db.session.commit()

    key = _change(preview, "time_change")["key"]
    resp = _apply(client, [{"key": key, "action": "apply"}], rev=preview["rev"])
    assert resp.status_code == 409
    # The errorhandler's body skips spectree's response validation.
    assert GooglePullConflictOut.model_validate(resp.get_json()).rev == preview["rev"] + 1
    db.session.expire_all()
    assert db.session.get(Slot, slot.id).start_at == datetime(2026, 7, 4, 14)

    # a re-pull picks up the fresh rev and applies cleanly
    preview = _preview(client)
    key = _change(preview, "time_change")["key"]
    ok(_apply(client, [{"key": key, "action": "apply"}], rev=preview["rev"]))
    db.session.expire_all()
    assert db.session.get(Slot, slot.id).start_at == datetime(2026, 7, 4, 15)


def test_apply_attach_and_delete(client, seeded, synced, gcal):
    camp, slot = synced
    gcal.add_external("ext2", "Hra v lese", "2026-07-06T10:00:00", "2026-07-06T12:00:00")
    del gcal.events[slot.google_event_id]  # the managed event was deleted in Google

    resp = _apply(client, [
        {"key": "new:ext2", "action": "attach", "target_activity_id": seeded["activity_id"]},
        {"key": f"del:{slot.id}", "action": "apply"}])
    assert ok(resp)["applied"] == {
        "created_activities": 0, "imported_slots": 1, "updated": 0, "deleted": 1}

    db.session.expire_all()
    assert db.session.get(Slot, slot.id) is None
    attached = db.session.scalar(db.select(Slot).where(Slot.google_event_id == "ext2"))
    assert attached is not None and attached.activity_id == seeded["activity_id"]


def test_preview_pull_requires_connection(client, seeded, gcal):
    resp = client.get(f"/api/camps/{SLUG}/google/pull", headers=editor(SLUG))
    assert resp.status_code == 400
    assert "není připojen" in resp.get_json()["error"]


def test_apply_attach_rejects_foreign_activity(client, seeded, gcal):
    _connect(_camp(seeded))
    gcal.add_external("extf", "Cizí", "2026-07-05T10:00:00", "2026-07-05T12:00:00")
    foreign = Activity(camp_id=_new_camp("jina", date(2026, 8, 1)).id, title="Cizí aktivita")
    db.session.add(foreign)
    db.session.commit()

    resp = _apply(client, [{"key": "new:extf", "action": "attach", "target_activity_id": foreign.id}])
    assert resp.status_code == 400
    assert "nepatří" in resp.get_json()["error"]
    assert db.session.scalar(db.select(Slot).where(Slot.google_event_id == "extf")) is None


def test_unchecked_changes_are_skipped(client, synced, gcal):
    gcal.add_external("ext3", "Nezvolená", "2026-07-06T10:00:00", "2026-07-06T12:00:00")

    assert ok(_apply(client, []))["applied"] == {
        "created_activities": 0, "imported_slots": 0, "updated": 0, "deleted": 0}
    assert db.session.scalar(db.select(Slot).where(Slot.google_event_id == "ext3")) is None


# --- field mapping: garants↔location, attendants↔description, color↔category ---------
# The initials/location grammar is unit-tested above (test_match_initials_matrix /
# test_parse_location_matrix); the e2e tests here keep one round-trip per change kind.

def test_event_body_maps_garants_helpers_and_attendants(app, seeded):
    marek = add_org(seeded["camp_id"], "M", "Marek")
    petr = add_org(seeded["camp_id"], "P", "Petr")
    activity = db.session.get(Activity, seeded["activity_id"])
    activity.assignments = [
        ActivityAssignment(org_id=seeded["org_id"], role=OrgRole.garant),  # K
        ActivityAssignment(org_id=marek.id, role=OrgRole.garant),
        ActivityAssignment(org_id=petr.id, role=OrgRole.helper),
    ]
    slot = _slot(activity.id)
    slot.assignments = [SlotAssignment(org_id=marek.id), SlotAssignment(org_id=seeded["org_id"])]
    db.session.commit()

    body = google_client.event_body(slot)
    assert body["location"] == "K+M, P"      # garants joined by '+', then helpers
    assert body["description"] == "K, M"     # slot attendants, czech-sorted


def test_inbound_attendants_change_skips_unknown_orgs(client, seeded, synced, gcal):
    camp, slot = synced
    gcal.events[slot.google_event_id]["description"] = "K, ZZ"   # ZZ matches no camp org

    att = _change(_preview(client), "attendants_change")
    assert (att["old_initials"], att["new_initials"], att["unknown"]) == ([], ["K"], ["ZZ"])

    _apply(client, [{"key": att["key"], "action": "apply"}])
    db.session.expire_all()
    assert {a.org_id for a in db.session.get(Slot, slot.id).assignments} == {seeded["org_id"]}


def test_inbound_garant_change(client, seeded, synced, gcal):
    camp, slot = synced
    marek = add_org(camp.id, "M", "Marek")
    petr = add_org(camp.id, "P", "Petr")
    db.session.get(Activity, seeded["activity_id"]).assignments = [
        ActivityAssignment(org_id=petr.id, role=OrgRole.helper)]
    db.session.commit()
    gcal.events[slot.google_event_id]["location"] = "K+M, P"  # K,M garants; P helper

    gar = _change(_preview(client), "garant_change")
    assert set(gar["new_garants"]) == {"K", "M"} and gar["new_helpers"] == ["P"]
    assert gar["old_garants"] == [] and gar["old_helpers"] == ["P"]

    _apply(client, [{"key": gar["key"], "action": "apply"}])
    db.session.expire_all()
    activity = db.session.get(Activity, seeded["activity_id"])
    garants = {a.org_id for a in activity.assignments if a.role == OrgRole.garant}
    helpers = {a.org_id for a in activity.assignments if a.role == OrgRole.helper}
    assert garants == {seeded["org_id"], marek.id} and helpers == {petr.id}
    # the audit names only the role that changed, as a manual edit does
    row = db.session.scalars(db.select(AuditLog).filter_by(entity_type=EntityType.assignment)).one()
    assert row.changes == {"garant": [[], ["K", "M"]]}


@pytest.mark.parametrize("color_id, label", [("11", "Výstraha"), (None, "(bez kategorie)")])
def test_inbound_category_change(client, seeded, synced, gcal, color_id, label):
    camp, slot = synced   # the activity has the seeded category, its event colorId "10"
    red = Category(camp_id=camp.id, key="vystraha", label="Výstraha", color="#d50000", sort_order=1)
    db.session.add(red)
    db.session.commit()
    gcal.events[slot.google_event_id]["colorId"] = color_id   # "11" is #d50000, None removed

    cat = _change(_preview(client), "category_change")
    assert (cat["old_label"], cat["new_label"]) == ("Hra", label)

    _apply(client, [{"key": cat["key"], "action": "apply"}])
    db.session.expire_all()
    assert db.session.get(Activity, seeded["activity_id"]).category_id == (red.id if color_id else None)


def test_foreign_slot_event_importable_and_marker_overwritten(client, seeded, gcal):
    _connect(_camp(seeded))
    ev = gcal.add_external("foreign", "Cizí hra", "2026-07-05T10:00:00", "2026-07-05T12:00:00")
    ev["extendedProperties"] = {"private": {"cpSlotId": "99999"}}  # another camp's slot id

    preview = _preview(client)
    new = _change(preview, "new_event")
    assert new["foreign_slot"] is True  # surfaced so the UI can warn

    _apply(client, [{"key": new["key"], "action": "new"}], rev=preview["rev"])
    db.session.expire_all()
    slot = db.session.scalar(db.select(Slot).where(Slot.google_event_id == "foreign"))
    assert slot is not None

    google_sync.drain(_camp(seeded))  # the next push rewrites the marker to our slot id
    assert gcal.events["foreign"]["extendedProperties"]["private"]["cpSlotId"] == str(slot.id)


def test_pending_delete_event_not_reoffered_as_import(client, seeded, gcal):
    """A slot deleted here, whose Google delete hasn't drained yet, must not resurface as a
    foreign import candidate (its marker is our own now-gone slot id)."""
    camp = _camp(seeded)
    _connect(camp)
    url, hdr = f"/api/camps/{SLUG}/timeline", editor(SLUG)

    create = {"rev": camp.timeline_rev, "creates": [
        {"activity_id": seeded["activity_id"], "role": "main",
         "start_at": "2026-07-04T14:00:00", "end_at": "2026-07-04T16:00:00"}]}
    client.patch(url, json=create, headers=hdr)
    google_sync.drain(camp)
    slot = db.session.scalar(db.select(Slot))
    event_id = slot.google_event_id

    client.patch(url, json={"rev": camp.timeline_rev, "deletes": [slot.id]}, headers=hdr)
    assert event_id in gcal.events                 # not drained, so still in Google
    assert google_sync.pending_count(camp) == 1    # with its delete op queued

    preview = google_sync.preview_pull(camp)
    assert not any(c["kind"] == "new_event" for c in preview["changes"])


def test_import_respects_explicit_no_category(client, seeded, gcal):
    _connect(_camp(seeded))
    ev = gcal.add_external("extc", "Barevná", "2026-07-05T10:00:00", "2026-07-05T12:00:00")
    ev["colorId"] = "10"  # the seeded category's color

    new = _change(_preview(client), "new_event")
    assert new["category_id"] == seeded["cat_id"]  # preview pre-fills the color-inferred category

    # an explicit null wins: no color-inferred fallback at apply time
    _apply(client, [{"key": new["key"], "action": "new", "category_id": None}])
    db.session.expire_all()
    slot = db.session.scalar(db.select(Slot).where(Slot.google_event_id == "extc"))
    assert slot is not None and slot.activity.category_id is None


def test_preview_changes_sorted_by_start(client, seeded, gcal):
    _connect(_camp(seeded))
    gcal.add_external("c", "Třetí", "2026-07-06T18:00:00", "2026-07-06T19:00:00")
    gcal.add_external("a", "První", "2026-07-04T09:00:00", "2026-07-04T10:00:00")
    gcal.add_external("b", "Druhá", "2026-07-05T12:00:00", "2026-07-05T13:00:00")

    assert [c["summary"] for c in _preview(client)["changes"]] == ["První", "Druhá", "Třetí"]


def test_import_new_event_seeds_orgs(client, seeded, gcal):
    camp = _camp(seeded)
    marek = add_org(camp.id, "M", "Marek")
    _connect(camp)
    ev = gcal.add_external("ext9", "Šifrovačka", "2026-07-05T10:00:00", "2026-07-05T12:00:00")
    ev["location"] = "K, M"    # first comma item (K) = garant, the rest (M) = helper
    ev["description"] = "K"    # attendant

    new = _change(_preview(client), "new_event")
    assert new["garant_initials"] == ["K"] and new["helper_initials"] == ["M"]
    assert new["attendant_initials"] == ["K"]

    _apply(client, [{"key": new["key"], "action": "new"}])
    db.session.expire_all()
    slot = db.session.scalar(db.select(Slot).where(Slot.google_event_id == "ext9"))
    assert {a.org_id for a in slot.assignments} == {seeded["org_id"]}
    roles = {a.role: a.org_id for a in slot.activity.assignments}
    assert roles[OrgRole.garant] == seeded["org_id"]
    assert roles[OrgRole.helper] == marek.id


# --- concurrent drains (cron + manual "Synchronizovat nyní") -------------------------

def test_drain_skips_when_lock_held(queued, gcal, monkeypatch):
    """Another drain holding the per-camp lock delivers the queued op; this one bows out."""
    camp, _ = queued

    @contextmanager
    def _held(_camp):
        yield False

    monkeypatch.setattr(google_sync, "_drain_lock", _held)
    result = google_sync.drain(camp)

    assert result == {"pushed": 0, "failed": 0, "pending": 1}
    assert gcal.events == {}
    assert google_sync.pending_count(camp) == 1


def test_drain_op_removal_is_idempotent(queued, gcal, monkeypatch):
    """The op rows are bulk-deleted, so a row already removed by a drain that raced past
    the lock (only possible on SQLite) just matches nothing."""
    camp, _ = queued
    op_id = db.session.scalar(db.select(GoogleSyncOp.id))
    real_insert = gcal.insert

    def insert_then_yank(cal, body):
        db.session.execute(db.delete(GoogleSyncOp).where(GoogleSyncOp.id == op_id))
        return real_insert(cal, body)

    monkeypatch.setattr(gcal, "insert", insert_then_yank)
    google_sync.drain(camp)

    assert google_sync.pending_count(camp) == 0
    assert len(gcal.events) == 1
