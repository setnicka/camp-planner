"""Google Calendar sync — service + API tests.

The Google boundary (services/google_client) is replaced by an in-memory fake, so these
exercise enqueue → drain, the timezone round-trip, connect/disconnect, and the API
permission envelope without any network. event_body / parse_event_times are pure, so they
run for real.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from camp_planner.extensions import db
from camp_planner.models.camp import Camp
from camp_planner.models.google import GoogleSyncOp
from camp_planner.models.slot import Slot, SlotRole
from camp_planner.services import camps as camps_service
from camp_planner.services import google_client, google_sync

from tests.conftest import ADMIN, editor, viewer

CAL = "cal@group.calendar.google.com"


class FakeGoogle:
    """A stand-in calendar: records events by id, can be told to fail one call."""

    def __init__(self):
        self.events: dict[str, dict] = {}
        self._n = 0
        self.fail_next = False
        self.calls = {"insert": 0, "patch": 0, "delete": 0}

    def insert(self, calendar_id, body):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("boom")
        self.calls["insert"] += 1
        self._n += 1
        eid = f"evt{self._n}"
        self.events[eid] = {**body, "id": eid}
        return eid

    def patch(self, calendar_id, event_id, body):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("boom")
        self.calls["patch"] += 1
        self.events[event_id] = {**self.events.get(event_id, {}), **body, "id": event_id}

    def delete(self, calendar_id, event_id):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("boom")
        self.calls["delete"] += 1
        self.events.pop(event_id, None)

    def batch_push(self, ops):
        """Stand-in for google_client.batch_push: dispatch each op to insert/patch/delete (so
        the calls/events/fail_next bookkeeping stays identical), returning a PushResult per key."""
        results = {}
        for op in ops:
            try:
                if op.kind == "insert":
                    results[op.key] = google_client.PushResult(True, self.insert(op.calendar_id, op.body), None)
                elif op.kind == "patch":
                    self.patch(op.calendar_id, op.event_id, op.body)
                    results[op.key] = google_client.PushResult(True, None, None)
                else:
                    self.delete(op.calendar_id, op.event_id)
                    results[op.key] = google_client.PushResult(True, None, None)
            except Exception as exc:  # noqa: BLE001 — mirror real per-op failure capture
                results[op.key] = google_client.PushResult(False, None, str(exc))
        return results

    def list_events(self, calendar_id, sync_token):
        return list(self.events.values()), None

    def add_external(self, eid, summary, start, end):
        """A user-created event (no cpSlotId marker), as if added directly in Google."""
        self.events[eid] = {
            "id": eid, "summary": summary, "status": "confirmed",
            "start": {"dateTime": start, "timeZone": "Europe/Prague"},
            "end": {"dateTime": end, "timeZone": "Europe/Prague"},
        }
        return self.events[eid]


@pytest.fixture(autouse=True)
def _identity(app):
    """Calls made directly to services (not via the client) still hit audit.record, which
    reads g.identity. Provide one on the app context; client requests get their own from
    the X-Remote-* headers, so this doesn't leak into them."""
    from flask import g

    from camp_planner.auth.identity import build_identity
    g.identity = build_identity(user_id="tester", is_admin=True)


# A trimmed stand-in for Google's fixed event palette (id → background hex).
FAKE_PALETTE = {"1": "#7986cb", "2": "#33b679", "8": "#616161", "10": "#0b8043", "11": "#d50000"}


@pytest.fixture
def gcal(app, monkeypatch):
    """Enable the feature and route the adapter at an in-memory fake calendar."""
    fake = FakeGoogle()
    monkeypatch.setattr(google_client, "is_configured", lambda: True)
    monkeypatch.setattr(google_client, "service_account_email", lambda: "sa@test.iam")
    monkeypatch.setattr(google_client, "verify_access", lambda calendar_id: None)
    monkeypatch.setattr(google_client, "batch_push", fake.batch_push)
    monkeypatch.setattr(google_client, "list_events", fake.list_events)
    monkeypatch.setattr(google_client, "color_palette", lambda: FAKE_PALETTE)
    return fake


def _camp(seeded) -> Camp:
    return db.session.get(Camp, seeded["camp_id"])


def _make_slot(activity_id, start, end, role=SlotRole.main) -> Slot:
    slot = Slot(activity_id=activity_id, start_at=start, end_at=end, role=role)
    db.session.add(slot)
    db.session.commit()
    return slot


# --- pure payload / timezone ---------------------------------------------------------

def test_event_body_uses_camp_timezone(app, seeded):
    slot = _make_slot(seeded["activity_id"], datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))
    body = google_client.event_body(slot)
    assert body["start"] == {"dateTime": "2026-07-04T14:00:00", "timeZone": "Europe/Prague"}
    assert body["end"]["dateTime"] == "2026-07-04T16:00:00"
    assert body["summary"] == "Akce"
    assert body["extendedProperties"]["private"]["cpSlotId"] == str(slot.id)


def test_prep_slot_summary_suffix(app, seeded):
    slot = _make_slot(seeded["activity_id"], datetime(2026, 7, 4, 8, 0),
                      datetime(2026, 7, 4, 9, 0), role=SlotRole.prep)
    assert google_client.event_body(slot)["summary"] == "Akce (příprava)"


def test_parse_event_times_roundtrip(app, seeded):
    # 14:00 local in Prague is +02:00 in July; parse must return the naive local 14:00.
    times = google_client.parse_event_times(
        {"start": {"dateTime": "2026-07-04T14:00:00+02:00"},
         "end": {"dateTime": "2026-07-04T16:00:00+02:00"}},
        "Europe/Prague",
    )
    assert times == (datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))


def test_parse_event_times_honors_field_timezone(app, seeded):
    # offset-less dateTime + a per-field timeZone → localized via that tz, then to camp tz.
    # 09:00 New York (EDT, -04:00) on 2026-07-04 == 15:00 Prague (CEST, +02:00).
    times = google_client.parse_event_times(
        {"start": {"dateTime": "2026-07-04T09:00:00", "timeZone": "America/New_York"},
         "end": {"dateTime": "2026-07-04T11:00:00", "timeZone": "America/New_York"}},
        "Europe/Prague",
    )
    assert times == (datetime(2026, 7, 4, 15, 0), datetime(2026, 7, 4, 17, 0))


def test_parse_event_times_floating_is_camp_local(app, seeded):
    # no offset and no timeZone → treated as already camp-local wall-clock
    times = google_client.parse_event_times(
        {"start": {"dateTime": "2026-07-04T14:00:00"}, "end": {"dateTime": "2026-07-04T16:00:00"}},
        "Europe/Prague",
    )
    assert times == (datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))


def test_parse_all_day_event_returns_none(app, seeded):
    assert google_client.parse_event_times(
        {"start": {"date": "2026-07-04"}, "end": {"date": "2026-07-05"}}, "Europe/Prague") is None


# --- batch_push (the real batched HTTP path, with a fake Google service) --------------
# The gcal fixture stubs batch_push wholesale, so the chunking + callback wiring + 404/410
# handling that batch_push itself adds is exercised only here, against a fake calendar service.

def _http_error(status):
    from googleapiclient.errors import HttpError  # available; only the network is faked

    resp = type("Resp", (), {"status": status, "reason": "x"})()
    return HttpError(resp, b"{}")


class _FakeBatchService:
    """Mimics the slice of the Google client batch_push touches: events() request builders,
    new_batch_http_request(callback), and a batch whose execute() fires the callback per add()."""

    def __init__(self):
        self.batch_count = 0

    def events(self):
        return self

    # request builders return a (response, exception) plan the fake batch will replay
    def insert(self, calendarId, body):
        return ({"id": "evt-" + body["summary"]}, None)

    def patch(self, calendarId, eventId, body):
        return ({"id": eventId}, None)

    def delete(self, calendarId, eventId):
        if eventId == "gone":
            return (None, _http_error(404))   # already deleted in Google
        if eventId == "boom":
            return (None, _http_error(500))   # a real failure
        return ({}, None)

    def new_batch_http_request(self, callback):
        self.batch_count += 1
        return _FakeBatch(callback)


class _FakeBatch:
    def __init__(self, callback):
        self.callback = callback
        self.added = []

    def add(self, request, request_id):
        self.added.append((request_id, request))

    def execute(self):
        for request_id, (response, exception) in self.added:
            self.callback(request_id, response, exception)


def test_batch_push_chunks_and_maps_outcomes(monkeypatch):
    svc = _FakeBatchService()
    monkeypatch.setattr(google_client, "client", lambda: svc)
    Op = google_client.PushOp

    ops = [Op(key=f"i{i}", kind="insert", calendar_id=CAL, body={"summary": str(i)}) for i in range(120)]
    ops += [Op(key="p", kind="patch", calendar_id=CAL, event_id="evtP", body={"summary": "P"}),
            Op(key="dgone", kind="delete", calendar_id=CAL, event_id="gone"),
            Op(key="dboom", kind="delete", calendar_id=CAL, event_id="boom")]

    results = google_client.batch_push(ops)

    assert svc.batch_count == 5                       # 123 ops at ≤25/batch → 5 round-trips
    assert results["i0"].ok and results["i0"].event_id == "evt-0"   # insert id captured
    assert results["p"].ok                            # patch succeeded
    assert results["dgone"].ok and results["dgone"].event_id is None  # 404 → already gone → success
    assert not results["dboom"].ok and results["dboom"].error        # 500 → genuine failure
    assert len(results) == len(ops)                   # every op got an outcome


# --- connect / disconnect ------------------------------------------------------------

def test_connect_queues_full_export_then_drain(app, seeded, gcal):
    camp = _camp(seeded)
    _make_slot(seeded["activity_id"], datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))
    _make_slot(seeded["activity_id"], datetime(2026, 7, 5, 14, 0), datetime(2026, 7, 5, 16, 0))

    camps_service.set_google_calendar(camp, CAL)
    assert camp.google_calendar_id == CAL
    assert google_sync.pending_count(camp) == 2  # one upsert per existing slot

    result = google_sync.drain(camp)
    assert result == {"pushed": 2, "failed": 0, "pending": 0}
    assert len(gcal.events) == 2
    assert all(s.google_event_id for s in db.session.scalars(db.select(Slot)).all())


def test_disconnect_forgets_mapping_and_queue(app, seeded, gcal):
    camp = _camp(seeded)
    slot = _make_slot(seeded["activity_id"], datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))
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
    _make_slot(seeded["activity_id"], datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))
    _make_slot(seeded["activity_id"], datetime(2026, 7, 5, 14, 0), datetime(2026, 7, 5, 16, 0))

    assert google_sync.resync_all(camp) == {"queued": 2}
    assert google_sync.pending_count(camp) == 2          # one upsert per slot
    assert google_sync.resync_all(camp) == {"queued": 2}  # idempotent — dedupes against queued upserts
    assert google_sync.pending_count(camp) == 2

    google_sync.drain(camp)
    assert len(gcal.events) == 2


def test_resync_all_noop_when_not_connected(app, seeded):
    camp = _camp(seeded)
    _make_slot(seeded["activity_id"], datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))
    assert google_sync.resync_all(camp) == {"queued": 0}
    assert google_sync.pending_count(camp) == 0


def _new_camp(slug, start, length=3, window=240):
    camp = Camp(name=slug.upper(), slug=slug, start_date=start, length_days=length,
                window_start_min=window, snap_minutes=15)
    db.session.add(camp)
    db.session.commit()
    return camp


def test_connect_rejects_time_overlap_on_shared_calendar(app, seeded, gcal):
    from camp_planner.services import errors

    camp_a = _camp(seeded)  # 2026-07-04 .. 07-07
    camps_service.set_google_calendar(camp_a, CAL)

    overlapping = _new_camp("b", date(2026, 7, 6))      # 07-06..07-09 overlaps A
    with pytest.raises(errors.Invalid):
        camps_service.set_google_calendar(overlapping, CAL)
    assert overlapping.google_calendar_id is None       # not connected

    free = _new_camp("c", date(2026, 7, 20))            # no overlap → allowed
    camps_service.set_google_calendar(free, CAL)
    assert free.google_calendar_id == CAL


def test_change_days_rejected_when_overlap_on_shared_calendar(app, seeded, gcal):
    from camp_planner.services import errors

    camp_a = _camp(seeded)  # 07-04 .. 07-07
    camps_service.set_google_calendar(camp_a, CAL)
    camp_b = _new_camp("b", date(2026, 7, 20))
    camps_service.set_google_calendar(camp_b, CAL)

    # moving B onto A's dates (same shared calendar) is refused, leaving B untouched
    with pytest.raises(errors.Invalid):
        camps_service.save_camp_settings(camp_b, {"start_date": date(2026, 7, 5)}, allow_meta=False)
    db.session.expire_all()
    assert db.session.get(Camp, camp_b.id).start_date == date(2026, 7, 20)

    # moving B to a free slot is fine
    camps_service.save_camp_settings(camp_b, {"start_date": date(2026, 8, 1)}, allow_meta=False)
    db.session.expire_all()
    assert db.session.get(Camp, camp_b.id).start_date == date(2026, 8, 1)


def test_reconnect_adopts_existing_events(app, seeded, gcal):
    camp = _camp(seeded)
    slot = _make_slot(seeded["activity_id"], datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))
    camps_service.set_google_calendar(camp, CAL)
    google_sync.drain(camp)
    assert gcal.calls["insert"] == 1 and len(gcal.events) == 1
    event_id = db.session.get(Slot, slot.id).google_event_id

    camps_service.disconnect_google(camp)                  # leaves the event in Google
    assert db.session.get(Slot, slot.id).google_event_id is None
    assert len(gcal.events) == 1

    camps_service.set_google_calendar(camp, CAL)           # reconnect to the SAME calendar
    assert db.session.get(Slot, slot.id).google_event_id == event_id  # adopted by cpSlotId
    google_sync.drain(camp)
    assert gcal.calls["insert"] == 1                       # no duplicate insert
    assert gcal.calls["patch"] >= 1 and len(gcal.events) == 1


def test_enqueue_is_noop_when_not_connected(app, seeded):
    camp = _camp(seeded)
    slot = _make_slot(seeded["activity_id"], datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))
    google_sync.enqueue_upsert(camp, slot)
    db.session.commit()
    assert db.session.scalar(db.select(db.func.count()).select_from(GoogleSyncOp)) == 0


# --- timeline edits flow through to Google -------------------------------------------

def _connect(camp):
    camp.google_calendar_id = CAL
    db.session.commit()


def test_timeline_create_move_delete(client, seeded, gcal):
    camp = _camp(seeded)
    _connect(camp)
    hdr = editor(seeded["slug"])

    # create a slot via the timeline PATCH → one queued upsert, drained to one event
    body = {"rev": camp.timeline_rev, "creates": [
        {"activity_id": seeded["activity_id"], "role": "main",
         "start_at": "2026-07-04T14:00:00", "end_at": "2026-07-04T16:00:00"}]}
    resp = client.patch(f"/api/camps/{seeded['slug']}/timeline", json=body, headers=hdr)
    assert resp.status_code == 200
    google_sync.drain(camp)
    assert len(gcal.events) == 1
    slot = db.session.scalar(db.select(Slot))
    event_id = slot.google_event_id
    assert event_id and gcal.events[event_id]["start"]["dateTime"] == "2026-07-04T14:00:00"

    # move it → patch the same event (no new event)
    body = {"rev": camp.timeline_rev, "moves": [
        {"slot_id": slot.id, "start_at": "2026-07-04T15:00:00", "end_at": "2026-07-04T17:00:00"}]}
    assert client.patch(f"/api/camps/{seeded['slug']}/timeline", json=body, headers=hdr).status_code == 200
    google_sync.drain(camp)
    assert len(gcal.events) == 1
    assert gcal.events[event_id]["start"]["dateTime"] == "2026-07-04T15:00:00"

    # delete it → the event is removed
    body = {"rev": camp.timeline_rev, "deletes": [slot.id]}
    assert client.patch(f"/api/camps/{seeded['slug']}/timeline", json=body, headers=hdr).status_code == 200
    google_sync.drain(camp)
    assert gcal.events == {}


def test_activity_rename_repushes_slot_events(app, seeded, gcal):
    from camp_planner.models.activity import Activity
    from camp_planner.schemas import ActivityUpdate
    from camp_planner.services import activities

    camp = _camp(seeded)
    _connect(camp)
    slot = _make_slot(seeded["activity_id"], datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))
    google_sync.enqueue_upsert(camp, slot)
    db.session.commit()
    google_sync.drain(camp)

    activity = db.session.get(Activity, seeded["activity_id"])
    activities.update_activity(activity, ActivityUpdate(title="Nová akce"))
    assert google_sync.pending_count(camp) == 1  # rename re-pushed the slot's event
    google_sync.drain(camp)
    assert gcal.events[slot.google_event_id]["summary"] == "Nová akce"


def test_enqueue_dedupes_ops_per_slot(app, seeded, gcal):
    camp = _camp(seeded)
    _connect(camp)
    slot = _make_slot(seeded["activity_id"], datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))
    for _ in range(3):  # three upserts for the same slot
        google_sync.enqueue_upsert(camp, slot)
    db.session.commit()
    assert google_sync.pending_count(camp) == 1  # deduped at insert — only one row queued

    result = google_sync.drain(camp)
    assert result == {"pushed": 1, "failed": 0, "pending": 0}
    assert gcal.calls == {"insert": 1, "patch": 0, "delete": 0}  # a single API call
    assert len(gcal.events) == 1


def test_drain_dedupes_raced_duplicates(app, seeded, gcal):
    """drain() is the safety net for duplicate rows a concurrent request could race in
    (which the insert-time check can't see). Insert two raw duplicate upserts directly."""
    from camp_planner.models.google import GoogleSyncOp, SyncOpKind

    camp = _camp(seeded)
    _connect(camp)
    slot = _make_slot(seeded["activity_id"], datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))
    db.session.add_all([GoogleSyncOp(camp_id=camp.id, slot_id=slot.id, op=SyncOpKind.upsert),
                        GoogleSyncOp(camp_id=camp.id, slot_id=slot.id, op=SyncOpKind.upsert)])
    db.session.commit()

    result = google_sync.drain(camp)
    assert result == {"pushed": 1, "failed": 0, "pending": 0}
    assert gcal.calls["insert"] == 1


def test_helper_only_change_repushes(app, seeded, gcal):
    from camp_planner.models.activity import Activity, OrgRole
    from camp_planner.schemas import ActivityOrgsIn
    from camp_planner.services import activities

    camp = _camp(seeded)
    helper = _add_org(camp, "M", "Marek")
    db.session.commit()
    _connect(camp)
    slot = _make_slot(seeded["activity_id"], datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))
    google_sync.enqueue_upsert(camp, slot)
    db.session.commit()
    google_sync.drain(camp)  # event now synced, queue empty
    assert google_sync.pending_count(camp) == 0

    # change ONLY a helper (no garant) — LOCATION carries helpers, so this must re-push
    activity = db.session.get(Activity, seeded["activity_id"])
    activities.set_orgs(activity, ActivityOrgsIn(orgs=[{"org_id": helper.id, "role": OrgRole.helper}]))
    assert google_sync.pending_count(camp) == 1


def test_google_operations_are_logged(app, seeded, gcal, caplog):
    import logging

    camp = _camp(seeded)
    slot = _make_slot(seeded["activity_id"], datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))
    _connect(camp)
    google_sync.enqueue_upsert(camp, slot)
    db.session.commit()

    with caplog.at_level(logging.INFO, logger="camp_planner.services.google_sync"):
        google_sync.drain(camp)              # success → INFO "created event"
        gcal.fail_next = True
        google_sync.enqueue_upsert(camp, slot)  # re-push (a patch) that will fail
        db.session.commit()
        google_sync.drain(camp)              # failure → WARNING "push failed"

    assert "Google Calendar: created event" in caplog.text
    assert "Google Calendar push failed" in caplog.text


def test_status_surfaces_failed_ops(client, seeded, gcal):
    camp = _camp(seeded)
    slot = _make_slot(seeded["activity_id"], datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))
    _connect(camp)
    google_sync.enqueue_upsert(camp, slot)
    db.session.commit()

    gcal.fail_next = True
    google_sync.drain(camp)  # the insert fails → op stays with attempts/last_error

    body = client.get(f"/api/camps/{seeded['slug']}/google", headers=editor(seeded["slug"])).get_json()
    assert body["google"]["failed_ops"] == 1
    assert body["google"]["last_error"]  # the error text is exposed


def test_drain_failure_keeps_op_and_records_error(app, seeded, gcal):
    camp = _camp(seeded)
    slot = _make_slot(seeded["activity_id"], datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))
    _connect(camp)
    google_sync.enqueue_upsert(camp, slot)
    db.session.commit()

    gcal.fail_next = True
    result = google_sync.drain(camp)
    assert result["failed"] == 1 and result["pending"] == 1
    op = db.session.scalar(db.select(GoogleSyncOp))
    assert op.attempts == 1 and op.last_error

    result = google_sync.drain(camp)  # retry succeeds
    assert result["pushed"] == 1 and google_sync.pending_count(camp) == 0


# --- API permissions / feature gating ------------------------------------------------

def test_status_endpoint_requires_edit(client, seeded, gcal):
    assert client.get(f"/api/camps/{seeded['slug']}/google", headers=viewer(seeded["slug"])).status_code == 403
    resp = client.get(f"/api/camps/{seeded['slug']}/google", headers=editor(seeded["slug"]))
    assert resp.status_code == 200
    assert resp.get_json()["google"]["enabled"] is True


def test_connect_and_sync_via_api(client, seeded, gcal):
    slug = seeded["slug"]
    resp = client.put(f"/api/camps/{slug}/google", json={"calendar_id": CAL}, headers=editor(slug))
    assert resp.status_code == 200 and resp.get_json()["google"]["connected"] is True

    resp = client.post(f"/api/camps/{slug}/google/sync", headers=editor(slug))
    assert resp.status_code == 200 and resp.get_json()["result"]["failed"] == 0


def test_resync_via_api(client, seeded, gcal):
    slug = seeded["slug"]
    camp = _camp(seeded)
    _connect(camp)
    _make_slot(seeded["activity_id"], datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))

    assert client.post(f"/api/camps/{slug}/google/resync", headers=viewer(slug)).status_code == 403
    resp = client.post(f"/api/camps/{slug}/google/resync", headers=editor(slug))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["result"]["queued"] == 1 and body["google"]["pending_ops"] == 1


def test_feature_disabled_rejects_connect(client, seeded):
    # no gcal fixture → is_configured() is False (no GOOGLE_SERVICE_ACCOUNT_JSON)
    resp = client.put(f"/api/camps/{seeded['slug']}/google",
                      json={"calendar_id": CAL}, headers=ADMIN)
    assert resp.status_code == 400
    assert "nastaven" in resp.get_json()["error"]


# --- inbound (Google → Planner) reviewed import --------------------------------------

def _connected_with_event(seeded):
    """Connect the camp and push one slot, returning (camp, slot) with slot.google_event_id."""
    camp = _camp(seeded)
    _connect(camp)
    slot = _make_slot(seeded["activity_id"], datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))
    google_sync.enqueue_upsert(camp, slot)
    db.session.commit()
    google_sync.drain(camp)
    return camp, slot


def test_preview_classifies_changes(client, seeded, gcal):
    camp, slot = _connected_with_event(seeded)
    # a Google-side time edit on our event, and a brand-new user event
    gcal.events[slot.google_event_id]["start"]["dateTime"] = "2026-07-04T15:00:00"
    gcal.events[slot.google_event_id]["end"]["dateTime"] = "2026-07-04T17:00:00"
    gcal.add_external("ext1", "Táborák", "2026-07-06T20:00:00", "2026-07-06T22:00:00")

    resp = client.get(f"/api/camps/{seeded['slug']}/google/pull", headers=editor(seeded["slug"]))
    assert resp.status_code == 200
    body = resp.get_json()
    kinds = {c["kind"]: c for c in body["changes"]}
    assert set(kinds) == {"time_change", "new_event"}
    assert kinds["new_event"]["summary"] == "Táborák"
    assert kinds["time_change"]["new_start"].endswith("15:00:00")


def test_apply_time_change_and_import_new(client, seeded, gcal):
    camp, slot = _connected_with_event(seeded)
    gcal.events[slot.google_event_id]["start"]["dateTime"] = "2026-07-04T15:00:00"
    gcal.events[slot.google_event_id]["end"]["dateTime"] = "2026-07-04T17:00:00"
    gcal.add_external("ext1", "Táborák", "2026-07-06T20:00:00", "2026-07-06T22:00:00")

    decisions = [
        {"key": f"time:{slot.id}", "action": "apply"},
        {"key": "new:ext1", "action": "new", "category_id": seeded["cat_id"]},
    ]
    resp = client.post(f"/api/camps/{seeded['slug']}/google/pull",
                       json={"decisions": decisions}, headers=editor(seeded["slug"]))
    assert resp.status_code == 200
    assert resp.get_json()["applied"] == {
        "created_activities": 1, "imported_slots": 1, "updated": 1, "deleted": 0}

    db.session.expire_all()
    assert db.session.get(Slot, slot.id).start_at == datetime(2026, 7, 4, 15, 0)
    imported = db.session.scalar(db.select(Slot).where(Slot.google_event_id == "ext1"))
    assert imported is not None and imported.activity.title == "Táborák"
    # importing queued an upsert so the next drain stamps the cpSlotId marker on ext1
    google_sync.drain(_camp(seeded))
    assert gcal.events["ext1"]["extendedProperties"]["private"]["cpSlotId"] == str(imported.id)


def test_apply_pull_stale_rev_conflicts(client, seeded, gcal):
    from camp_planner.services.timeline import bump_timeline_rev

    camp, slot = _connected_with_event(seeded)
    gcal.events[slot.google_event_id]["start"]["dateTime"] = "2026-07-04T15:00:00"
    gcal.events[slot.google_event_id]["end"]["dateTime"] = "2026-07-04T17:00:00"

    body = client.get(f"/api/camps/{seeded['slug']}/google/pull",
                      headers=editor(seeded["slug"])).get_json()
    stale_rev = body["rev"]
    tc_key = next(c["key"] for c in body["changes"] if c["kind"] == "time_change")

    bump_timeline_rev(_camp(seeded))  # a concurrent timeline edit moves the lock
    db.session.commit()

    resp = client.post(f"/api/camps/{seeded['slug']}/google/pull",
                       json={"rev": stale_rev, "decisions": [{"key": tc_key, "action": "apply"}]},
                       headers=editor(seeded["slug"]))
    assert resp.status_code == 409  # stale rev rejected; nothing applied
    db.session.expire_all()
    assert db.session.get(Slot, slot.id).start_at == datetime(2026, 7, 4, 14, 0)

    # re-pull picks up the fresh rev and applies cleanly
    body2 = client.get(f"/api/camps/{seeded['slug']}/google/pull",
                       headers=editor(seeded["slug"])).get_json()
    tc_key2 = next(c["key"] for c in body2["changes"] if c["kind"] == "time_change")
    resp2 = client.post(f"/api/camps/{seeded['slug']}/google/pull",
                        json={"rev": body2["rev"], "decisions": [{"key": tc_key2, "action": "apply"}]},
                        headers=editor(seeded["slug"]))
    assert resp2.status_code == 200
    db.session.expire_all()
    assert db.session.get(Slot, slot.id).start_at == datetime(2026, 7, 4, 15, 0)


def test_apply_attach_and_delete(client, seeded, gcal):
    camp, slot = _connected_with_event(seeded)
    gcal.add_external("ext2", "Hra v lese", "2026-07-06T10:00:00", "2026-07-06T12:00:00")
    del gcal.events[slot.google_event_id]  # the managed event was deleted in Google

    decisions = [
        {"key": "new:ext2", "action": "attach", "target_activity_id": seeded["activity_id"]},
        {"key": f"del:{slot.id}", "action": "apply"},
    ]
    resp = client.post(f"/api/camps/{seeded['slug']}/google/pull",
                       json={"decisions": decisions}, headers=editor(seeded["slug"]))
    assert resp.status_code == 200
    assert resp.get_json()["applied"] == {
        "created_activities": 0, "imported_slots": 1, "updated": 0, "deleted": 1}

    db.session.expire_all()
    assert db.session.get(Slot, slot.id) is None  # deleted
    attached = db.session.scalar(db.select(Slot).where(Slot.google_event_id == "ext2"))
    assert attached is not None and attached.activity_id == seeded["activity_id"]


def test_unchecked_changes_are_skipped(client, seeded, gcal):
    camp, slot = _connected_with_event(seeded)
    gcal.add_external("ext3", "Nezvolená", "2026-07-06T10:00:00", "2026-07-06T12:00:00")

    # apply with an empty decision list → nothing happens
    resp = client.post(f"/api/camps/{seeded['slug']}/google/pull",
                       json={"decisions": []}, headers=editor(seeded["slug"]))
    assert resp.get_json()["applied"] == {
        "created_activities": 0, "imported_slots": 0, "updated": 0, "deleted": 0}
    assert db.session.scalar(db.select(Slot).where(Slot.google_event_id == "ext3")) is None


# --- field mapping: garants↔location, attendants↔description, color↔category ---------

def _add_org(camp, initials, name):
    from camp_planner.models.org import Org
    org = Org(camp_id=camp.id, name=name, initials=initials)
    db.session.add(org)
    db.session.flush()
    return org


def test_event_body_maps_garants_and_attendants(app, seeded):
    from camp_planner.models.activity import Activity, ActivityAssignment, OrgRole
    from camp_planner.models.slot import SlotAssignment

    camp = _camp(seeded)
    marek = _add_org(camp, "M", "Marek")
    activity = db.session.get(Activity, seeded["activity_id"])
    activity.assignments = [ActivityAssignment(org_id=seeded["org_id"], role=OrgRole.garant)]
    slot = _make_slot(activity.id, datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))
    slot.assignments = [SlotAssignment(org_id=seeded["org_id"]), SlotAssignment(org_id=marek.id)]
    db.session.commit()

    body = google_client.event_body(slot)
    assert body["location"] == "K"            # one garant, no helpers
    assert body["description"] == "K, M"      # slot attendants, czech-sorted


def test_event_body_location_format(app, seeded):
    from camp_planner.models.activity import Activity, ActivityAssignment, OrgRole

    camp = _camp(seeded)
    marek = _add_org(camp, "M", "Marek")
    petr = _add_org(camp, "P", "Petr")
    activity = db.session.get(Activity, seeded["activity_id"])
    activity.assignments = [
        ActivityAssignment(org_id=seeded["org_id"], role=OrgRole.garant),  # K
        ActivityAssignment(org_id=marek.id, role=OrgRole.garant),          # M
        ActivityAssignment(org_id=petr.id, role=OrgRole.helper),           # P
    ]
    slot = _make_slot(activity.id, datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))
    db.session.commit()
    # garants joined by '+', then helpers as comma items
    assert google_client.event_body(slot)["location"] == "K+M, P"


def test_event_color_snaps_to_palette(app, seeded, gcal):
    from camp_planner.models.activity import Activity
    activity = db.session.get(Activity, seeded["activity_id"])  # its category is #0b8043
    assert google_client.event_color_id(activity) == "10"       # exact palette match


def test_inbound_attendants_change(client, seeded, gcal):
    camp, slot = _connected_with_event(seeded)
    gcal.events[slot.google_event_id]["description"] = "K"  # attendant added in Google

    body = client.get(f"/api/camps/{seeded['slug']}/google/pull",
                      headers=editor(seeded["slug"])).get_json()
    att = next(c for c in body["changes"] if c["kind"] == "attendants_change")
    assert att["old_initials"] == [] and att["new_initials"] == ["K"] and not att["unknown"]

    client.post(f"/api/camps/{seeded['slug']}/google/pull",
                json={"decisions": [{"key": att["key"], "action": "apply"}]},
                headers=editor(seeded["slug"]))
    db.session.expire_all()
    assert {a.org_id for a in db.session.get(Slot, slot.id).assignments} == {seeded["org_id"]}


def test_inbound_orgs_split_on_plus(client, seeded, gcal):
    camp, slot = _connected_with_event(seeded)
    marek = _add_org(camp, "M", "Marek")
    db.session.commit()
    gcal.events[slot.google_event_id]["description"] = "K + M"  # plus-separated

    body = client.get(f"/api/camps/{seeded['slug']}/google/pull",
                      headers=editor(seeded["slug"])).get_json()
    att = next(c for c in body["changes"] if c["kind"] == "attendants_change")
    assert set(att["new_initials"]) == {"K", "M"} and not att["unknown"]

    client.post(f"/api/camps/{seeded['slug']}/google/pull",
                json={"decisions": [{"key": att["key"], "action": "apply"}]},
                headers=editor(seeded["slug"]))
    db.session.expire_all()
    assert {a.org_id for a in db.session.get(Slot, slot.id).assignments} == {seeded["org_id"], marek.id}


def test_inbound_orgs_strip_parens_and_split_on_space(client, seeded, gcal):
    from camp_planner.models.activity import Activity, OrgRole

    camp, slot = _connected_with_event(seeded)
    marek = _add_org(camp, "M", "Marek")
    petr = _add_org(camp, "P", "Petr")
    db.session.commit()
    # spaces separate; parens are ignored: first comma-item = garants, rest = helpers
    gcal.events[slot.google_event_id]["location"] = "(K M), (P)"
    gcal.events[slot.google_event_id]["description"] = "(K) M"

    body = client.get(f"/api/camps/{seeded['slug']}/google/pull",
                      headers=editor(seeded["slug"])).get_json()
    gar = next(c for c in body["changes"] if c["kind"] == "garant_change")
    assert set(gar["new_garants"]) == {"K", "M"} and gar["new_helpers"] == ["P"]
    att = next(c for c in body["changes"] if c["kind"] == "attendants_change")
    assert set(att["new_initials"]) == {"K", "M"} and not att["unknown"]

    for key in (gar["key"], att["key"]):
        client.post(f"/api/camps/{seeded['slug']}/google/pull",
                    json={"decisions": [{"key": key, "action": "apply"}]},
                    headers=editor(seeded["slug"]))
    db.session.expire_all()
    activity = db.session.get(Activity, seeded["activity_id"])
    assert {a.org_id for a in activity.assignments if a.role == OrgRole.garant} == {seeded["org_id"], marek.id}
    assert {a.org_id for a in activity.assignments if a.role == OrgRole.helper} == {petr.id}


def test_parse_location_parens_act_as_commas(app, seeded):
    from camp_planner.services.google_sync import _parse_location

    camp = _camp(seeded)  # seeded already has org "K"
    for ini, name in [("H", "Hugo"), ("Á", "Ája"), ("B", "Bob"), ("L", "Lola")]:
        _add_org(camp, ini, name)
    db.session.commit()
    by_id = {o.id: o.initials for o in camp.orgs}

    gar, helpers, unknown = _parse_location(camp, "H (Á, B, L)")
    assert [by_id[i] for i in gar] == ["H"]                  # first item → garant
    assert {by_id[i] for i in helpers} == {"Á", "B", "L"}    # parenthesised → helpers
    assert unknown == []


def test_inbound_garant_change(client, seeded, gcal):
    from camp_planner.models.activity import Activity, OrgRole

    camp, slot = _connected_with_event(seeded)
    marek = _add_org(camp, "M", "Marek")
    petr = _add_org(camp, "P", "Petr")
    db.session.commit()
    gcal.events[slot.google_event_id]["location"] = "K+M, P"  # K,M garants; P helper

    body = client.get(f"/api/camps/{seeded['slug']}/google/pull",
                      headers=editor(seeded["slug"])).get_json()
    gar = next(c for c in body["changes"] if c["kind"] == "garant_change")
    assert set(gar["new_garants"]) == {"K", "M"} and gar["new_helpers"] == ["P"]
    assert gar["old_garants"] == [] and gar["old_helpers"] == []  # none before

    client.post(f"/api/camps/{seeded['slug']}/google/pull",
                json={"decisions": [{"key": gar["key"], "action": "apply"}]},
                headers=editor(seeded["slug"]))
    db.session.expire_all()
    activity = db.session.get(Activity, seeded["activity_id"])
    garants = {a.org_id for a in activity.assignments if a.role == OrgRole.garant}
    helpers = {a.org_id for a in activity.assignments if a.role == OrgRole.helper}
    assert garants == {seeded["org_id"], marek.id} and helpers == {petr.id}


def test_inbound_unknown_orgs_flagged_and_skipped(client, seeded, gcal):
    camp, slot = _connected_with_event(seeded)
    gcal.events[slot.google_event_id]["description"] = "K, ZZ"  # ZZ matches no camp org

    body = client.get(f"/api/camps/{seeded['slug']}/google/pull",
                      headers=editor(seeded["slug"])).get_json()
    att = next(c for c in body["changes"] if c["kind"] == "attendants_change")
    assert att["unknown"] == ["ZZ"] and att["new_initials"] == ["K"]

    client.post(f"/api/camps/{seeded['slug']}/google/pull",
                json={"decisions": [{"key": att["key"], "action": "apply"}]},
                headers=editor(seeded["slug"]))
    db.session.expire_all()
    assert {a.org_id for a in db.session.get(Slot, slot.id).assignments} == {seeded["org_id"]}


def test_inbound_category_change(client, seeded, gcal):
    from camp_planner.models.activity import Activity
    from camp_planner.models.camp import Category

    camp, slot = _connected_with_event(seeded)
    red = Category(camp_id=camp.id, key="vystraha", label="Výstraha", color="#d50000", sort_order=1)
    db.session.add(red)
    db.session.commit()
    gcal.events[slot.google_event_id]["colorId"] = "11"  # palette "11" == #d50000 → the red category

    body = client.get(f"/api/camps/{seeded['slug']}/google/pull",
                      headers=editor(seeded["slug"])).get_json()
    cat = next(c for c in body["changes"] if c["kind"] == "category_change")
    assert cat["old_label"] == "Hra" and cat["new_label"] == "Výstraha"

    client.post(f"/api/camps/{seeded['slug']}/google/pull",
                json={"decisions": [{"key": cat["key"], "action": "apply"}]},
                headers=editor(seeded["slug"]))
    db.session.expire_all()
    assert db.session.get(Activity, seeded["activity_id"]).category_id == red.id


def test_inbound_category_cleared(client, seeded, gcal):
    from camp_planner.models.activity import Activity

    camp, slot = _connected_with_event(seeded)  # activity has the seeded category (#0b8043)
    # the synced event carries colorId "10"; the user removes the colour in Google
    gcal.events[slot.google_event_id].pop("colorId", None)

    body = client.get(f"/api/camps/{seeded['slug']}/google/pull",
                      headers=editor(seeded["slug"])).get_json()
    cat = next(c for c in body["changes"] if c["kind"] == "category_change")
    assert cat["new_label"] == "(bez kategorie)" and cat["old_label"] == "Hra"

    client.post(f"/api/camps/{seeded['slug']}/google/pull",
                json={"decisions": [{"key": cat["key"], "action": "apply"}]},
                headers=editor(seeded["slug"]))
    db.session.expire_all()
    assert db.session.get(Activity, seeded["activity_id"]).category_id is None


def test_foreign_slot_event_importable_and_marker_overwritten(client, seeded, gcal):
    camp = _camp(seeded)
    _connect(camp)
    ev = gcal.add_external("foreign", "Cizí hra", "2026-07-05T10:00:00", "2026-07-05T12:00:00")
    ev["extendedProperties"] = {"private": {"cpSlotId": "99999"}}  # another camp's slot id

    body = client.get(f"/api/camps/{seeded['slug']}/google/pull",
                      headers=editor(seeded["slug"])).get_json()
    new = next(c for c in body["changes"] if c["kind"] == "new_event")
    assert new["foreign_slot"] is True  # surfaced so the UI can warn

    client.post(f"/api/camps/{seeded['slug']}/google/pull",
                json={"rev": body["rev"], "decisions": [{"key": new["key"], "action": "new"}]},
                headers=editor(seeded["slug"]))
    db.session.expire_all()
    slot = db.session.scalar(db.select(Slot).where(Slot.google_event_id == "foreign"))
    assert slot is not None

    google_sync.drain(_camp(seeded))  # the next push rewrites the foreign marker to our slot id
    assert gcal.events["foreign"]["extendedProperties"]["private"]["cpSlotId"] == str(slot.id)


def test_pending_delete_event_not_reoffered_as_import(client, seeded, gcal):
    """A slot deleted here, whose Google delete hasn't drained yet, must not resurface as a
    foreign import candidate (its marker is our own now-gone slot id)."""
    camp = _camp(seeded)
    _connect(camp)
    hdr = editor(seeded["slug"])

    create = {"rev": camp.timeline_rev, "creates": [
        {"activity_id": seeded["activity_id"], "role": "main",
         "start_at": "2026-07-04T14:00:00", "end_at": "2026-07-04T16:00:00"}]}
    client.patch(f"/api/camps/{seeded['slug']}/timeline", json=create, headers=hdr)
    google_sync.drain(camp)
    slot = db.session.scalar(db.select(Slot))
    event_id = slot.google_event_id

    # delete the slot but DON'T drain — the event lingers in Google with the gone slot's marker
    delete = {"rev": camp.timeline_rev, "deletes": [slot.id]}
    client.patch(f"/api/camps/{seeded['slug']}/timeline", json=delete, headers=hdr)
    assert event_id in gcal.events                 # not yet removed from Google
    assert google_sync.pending_count(camp) == 1    # a delete op is queued for it

    preview = google_sync.preview_pull(camp)        # must NOT re-offer it as a new/foreign import
    assert not any(c["kind"] == "new_event" for c in preview["changes"])


def test_import_skips_out_of_timeframe_and_too_long(client, seeded, gcal):
    camp = _camp(seeded)
    _connect(camp)
    # camp window is 2026-07-04 04:00 .. 2026-07-07 04:00
    gcal.add_external("before", "Před táborem", "2026-07-01T10:00:00", "2026-07-01T12:00:00")
    gcal.add_external("during", "Během", "2026-07-05T10:00:00", "2026-07-05T12:00:00")
    gcal.add_external("span", "Celý tábor", "2026-07-04T08:00:00", "2026-07-07T08:00:00")  # > 48h

    body = client.get(f"/api/camps/{seeded['slug']}/google/pull",
                      headers=editor(seeded["slug"])).get_json()
    summaries = {c["summary"] for c in body["changes"] if c["kind"] == "new_event"}
    assert summaries == {"Během"}  # out-of-window and the >48h span event are skipped


def test_import_respects_explicit_no_category(client, seeded, gcal):
    camp = _camp(seeded)
    _connect(camp)
    ev = gcal.add_external("extc", "Barevná", "2026-07-05T10:00:00", "2026-07-05T12:00:00")
    ev["colorId"] = "10"  # FAKE_PALETTE "10" == #0b8043 == the seeded category's color

    body = client.get(f"/api/camps/{seeded['slug']}/google/pull",
                      headers=editor(seeded["slug"])).get_json()
    new = next(c for c in body["changes"] if c["kind"] == "new_event")
    assert new["category_id"] == seeded["cat_id"]  # preview pre-fills the color-inferred category

    # user explicitly clears it → null must win (no color-inferred fallback at apply time)
    client.post(f"/api/camps/{seeded['slug']}/google/pull",
                json={"decisions": [{"key": new["key"], "action": "new", "category_id": None}]},
                headers=editor(seeded["slug"]))
    db.session.expire_all()
    slot = db.session.scalar(db.select(Slot).where(Slot.google_event_id == "extc"))
    assert slot is not None and slot.activity.category_id is None


def test_preview_changes_sorted_by_start(client, seeded, gcal):
    camp = _camp(seeded)
    _connect(camp)
    # add three in-window events out of chronological order
    gcal.add_external("c", "Třetí", "2026-07-06T18:00:00", "2026-07-06T19:00:00")
    gcal.add_external("a", "První", "2026-07-04T09:00:00", "2026-07-04T10:00:00")
    gcal.add_external("b", "Druhá", "2026-07-05T12:00:00", "2026-07-05T13:00:00")

    body = client.get(f"/api/camps/{seeded['slug']}/google/pull",
                      headers=editor(seeded["slug"])).get_json()
    starts = [c["new_start"] for c in body["changes"]]
    assert starts == sorted(starts)


def test_import_new_event_seeds_orgs(client, seeded, gcal):
    from camp_planner.models.activity import OrgRole

    camp = _camp(seeded)
    marek = _add_org(camp, "M", "Marek")
    db.session.commit()
    _connect(camp)
    ev = gcal.add_external("ext9", "Šifrovačka", "2026-07-05T10:00:00", "2026-07-05T12:00:00")
    ev["location"] = "K, M"    # first comma item (K) = garant, the rest (M) = helper
    ev["description"] = "K"    # attendant

    body = client.get(f"/api/camps/{seeded['slug']}/google/pull",
                      headers=editor(seeded["slug"])).get_json()
    new = next(c for c in body["changes"] if c["kind"] == "new_event")
    assert new["garant_initials"] == ["K"] and new["helper_initials"] == ["M"]
    assert new["attendant_initials"] == ["K"]

    client.post(f"/api/camps/{seeded['slug']}/google/pull",
                json={"decisions": [{"key": new["key"], "action": "new"}]},
                headers=editor(seeded["slug"]))
    db.session.expire_all()
    slot = db.session.scalar(db.select(Slot).where(Slot.google_event_id == "ext9"))
    assert {a.org_id for a in slot.assignments} == {seeded["org_id"]}                  # attendants
    roles = {a.role: a.org_id for a in slot.activity.assignments}
    assert roles[OrgRole.garant] == seeded["org_id"]   # first LOCATION item → garant
    assert roles[OrgRole.helper] == marek.id           # the rest → helpers


# --- concurrent drains (cron + manual "Synchronizovat nyní") -------------------------

def test_drain_skips_when_lock_held(client, seeded, gcal, monkeypatch):
    """When another drain holds the per-camp lock, drain bows out: nothing is pushed and the
    queued op is left for the holder to deliver."""
    from contextlib import contextmanager

    camp = _camp(seeded)
    _connect(camp)
    slot = _make_slot(seeded["activity_id"], datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))
    google_sync.enqueue_upsert(camp, slot)
    db.session.commit()
    assert google_sync.pending_count(camp) == 1

    @contextmanager
    def _held(_camp):
        yield False  # pretend a concurrent drain already holds the lock

    monkeypatch.setattr(google_sync, "_drain_lock", _held)
    result = google_sync.drain(camp)

    assert result == {"pushed": 0, "failed": 0, "pending": 1}
    assert gcal.events == {}                       # nothing delivered
    assert google_sync.pending_count(camp) == 1    # op still queued


def test_drain_op_removal_is_idempotent(client, seeded, gcal, monkeypatch):
    """The op rows are bulk-deleted, so a row already removed (by a drain that raced past the
    lock — only possible on SQLite) doesn't raise: the trailing delete just matches no rows."""
    camp = _camp(seeded)
    _connect(camp)
    slot = _make_slot(seeded["activity_id"], datetime(2026, 7, 4, 14, 0), datetime(2026, 7, 4, 16, 0))
    google_sync.enqueue_upsert(camp, slot)
    db.session.commit()
    op_id = db.session.scalar(db.select(GoogleSyncOp.id))

    real_insert = gcal.insert

    def insert_then_yank(cal, body):
        # mimic a concurrent drain that already deleted this op row before we get to remove it
        db.session.execute(db.delete(GoogleSyncOp).where(GoogleSyncOp.id == op_id))
        return real_insert(cal, body)

    monkeypatch.setattr(gcal, "insert", insert_then_yank)  # batch_push dispatches to fake.insert
    google_sync.drain(camp)  # trailing bulk delete hits 0 rows — must not raise

    assert google_sync.pending_count(camp) == 0
    assert len(gcal.events) == 1
