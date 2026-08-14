"""iCal feed: token guarding, the timeline filter grammar, and the RFC 5545 writer."""

from __future__ import annotations

from datetime import datetime

import pytest

from camp_planner.auth.identity import CampRole
from camp_planner.extensions import db
from camp_planner.models.activity import Activity, ActivityAssignment, OrgRole
from camp_planner.models.auth import ApiToken
from camp_planner.models.camp import Camp
from camp_planner.models.org import Org
from camp_planner.models.slot import Slot, SlotAssignment, SlotRole
from camp_planner.services import api_tokens
from camp_planner.services.errors import Invalid
from camp_planner.services.ical import _escape, _fold, parse_filters
from tests.conftest import make_camp


@pytest.fixture
def feed(seeded):
    """seeded + a second org, an uncategorized activity and three slots:
    main+prep of "Akce" (garant Karel, helper Marta) and one of "Volno…" (Marta attends)."""
    camp = db.session.get(Camp, seeded["camp_id"])
    marta = Org(camp_id=camp.id, name="Marta Nováková", initials="M")
    db.session.add(marta)
    db.session.flush()
    hra = db.session.get(Activity, seeded["activity_id"])   # category "hra"
    free = Activity(camp_id=camp.id, title="Volno, klid; pohoda")   # no category
    db.session.add_all([
        free,
        ActivityAssignment(activity_id=hra.id, org_id=seeded["org_id"], role=OrgRole.garant),
        ActivityAssignment(activity_id=hra.id, org_id=marta.id, role=OrgRole.helper),
    ])
    db.session.flush()
    s_prep = Slot(activity_id=hra.id, role=SlotRole.prep,
                  start_at=datetime(2026, 7, 4, 9, 0), end_at=datetime(2026, 7, 4, 10, 0))
    s_main = Slot(activity_id=hra.id, role=SlotRole.main,
                  start_at=datetime(2026, 7, 4, 10, 0), end_at=datetime(2026, 7, 4, 11, 30))
    s_free = Slot(activity_id=free.id, role=SlotRole.main,
                  start_at=datetime(2026, 7, 5, 14, 0), end_at=datetime(2026, 7, 5, 15, 0))
    db.session.add_all([s_prep, s_main, s_free])
    db.session.flush()
    db.session.add(SlotAssignment(slot_id=s_free.id, org_id=marta.id))
    _token, secret = api_tokens.create(camp, "cal", CampRole.viewer, "tester")
    return {**seeded, "marta_id": marta.id, "free_id": free.id, "secret": secret,
            "slot_prep": s_prep.id, "slot_main": s_main.id, "slot_free": s_free.id}


def _get(client, slug, secret, filters=()):
    query = [("filter", f) for f in filters]
    if secret is not None:
        query.append(("token", secret))
    return client.get(f"/ical/{slug}", query_string=query)


def _unfold(text: str) -> str:
    return text.replace("\r\n ", "")


# --- token guarding ---------------------------------------------------------------

def test_missing_or_unknown_token_is_401(client, feed):
    assert _get(client, feed["slug"], None).status_code == 401
    assert _get(client, feed["slug"], "cp_wrong").status_code == 401


def test_editor_token_is_403(client, feed):
    camp = db.session.get(Camp, feed["camp_id"])
    _token, secret = api_tokens.create(camp, "ed", CampRole.editor, "tester")
    assert _get(client, feed["slug"], secret).status_code == 403


def test_only_successful_requests_touch_last_used_at(client, feed):
    token = db.session.scalar(db.select(ApiToken).filter_by(camp_id=feed["camp_id"]))
    assert _get(client, "nope", feed["secret"]).status_code == 404
    assert token.last_used_at is None          # rejected probe is not a use
    assert _get(client, feed["slug"], feed["secret"]).status_code == 200
    assert token.last_used_at is not None


def test_foreign_camp_token_is_404(client, feed):
    other = make_camp(client, "u")
    camp = db.session.scalar(db.select(Camp).filter_by(slug=other["slug"]))
    _token, secret = api_tokens.create(camp, "cal", CampRole.viewer, "tester")
    assert _get(client, feed["slug"], secret).status_code == 404


# --- feed content -----------------------------------------------------------------

def test_feed_shape_and_content(client, feed):
    resp = _get(client, feed["slug"], feed["secret"])
    assert resp.status_code == 200
    assert resp.mimetype == "text/calendar"
    assert 'filename="t.ics"' in resp.headers["Content-Disposition"]
    text = resp.get_data(as_text=True)
    flat = _unfold(text)
    assert text.endswith("END:VCALENDAR\r\n")
    assert "X-WR-CALNAME:Tábor" in flat
    assert flat.count("BEGIN:VEVENT") == 3
    # naive local 10:00 in Europe/Prague (CEST) is 08:00Z
    assert "DTSTART:20260704T080000Z" in flat
    assert "SUMMARY:Akce (příprava)" in flat
    assert "SUMMARY:Akce\r\n" in flat
    assert "SUMMARY:Volno\\, klid\\; pohoda" in flat
    assert "CATEGORIES:Hra" in flat
    assert "DESCRIPTION:Garant: Karel\\nPomocníci: Marta Nováková" in flat
    assert "DESCRIPTION:Účast: Marta Nováková" in flat
    # events sorted by start: prep, main, free (UID keyed on camp id, not the mutable slug)
    uids = [f"UID:slot-{feed[k]}@camp-{feed['camp_id']}.camp-planner"
            for k in ("slot_prep", "slot_main", "slot_free")]
    positions = [flat.index(u) for u in uids]
    assert positions == sorted(positions)


def _uids(resp) -> set[str]:
    return {line.split("@")[0].removeprefix("UID:slot-")
            for line in _unfold(resp.get_data(as_text=True)).split("\r\n")
            if line.startswith("UID:")}


def test_filters(client, feed):
    slug, secret = feed["slug"], feed["secret"]
    prep, main, free = (str(feed[k]) for k in ("slot_prep", "slot_main", "slot_free"))
    karel, marta = str(feed["org_id"]), str(feed["marta_id"])

    assert _uids(_get(client, slug, secret, ["category:hra"])) == {prep, main}
    assert _uids(_get(client, slug, secret, ["category:_none"])) == {free}
    assert _uids(_get(client, slug, secret, ["category:hra", "category:_none"])) == {prep, main, free}
    # the garant facet spans garants and helpers, like the timeline chips
    assert _uids(_get(client, slug, secret, [f"garant:{karel}"])) == {prep, main}
    assert _uids(_get(client, slug, secret, [f"garant:{marta}"])) == {prep, main}
    assert _uids(_get(client, slug, secret, [f"attending:{marta}"])) == {free}
    assert _uids(_get(client, slug, secret, [f"activity:{feed['free_id']}"])) == {free}
    # types AND together
    assert _uids(_get(client, slug, secret, ["category:hra", f"attending:{marta}"])) == set()


def test_out_of_window_slot_is_not_exported(client, feed):
    # orphaned by a date change: invisible on the timeline, so kept out of the feed too
    db.session.add(Slot(activity_id=feed["free_id"], role=SlotRole.main,
                        start_at=datetime(2026, 7, 20, 10, 0), end_at=datetime(2026, 7, 20, 11, 0)))
    db.session.commit()
    resp = _get(client, feed["slug"], feed["secret"])
    assert _unfold(resp.get_data(as_text=True)).count("BEGIN:VEVENT") == 3


def test_straddling_slot_is_exported_like_the_timeline_shows_it(client, feed):
    # 02:00-05:00 crosses the 04:00 window start: the timeline renders it clipped, so
    # the feed keeps it (span_in_window would wrongly drop it)
    db.session.add(Slot(activity_id=feed["free_id"], role=SlotRole.main,
                        start_at=datetime(2026, 7, 4, 2, 0), end_at=datetime(2026, 7, 4, 5, 0)))
    db.session.commit()
    resp = _get(client, feed["slug"], feed["secret"])
    assert _unfold(resp.get_data(as_text=True)).count("BEGIN:VEVENT") == 4


def test_malformed_filter_is_400(client, feed):
    # grammar coverage lives in test_parse_filters; this only pins Invalid -> 400
    assert _get(client, feed["slug"], feed["secret"], ["foo:1"]).status_code == 400


# --- writer plumbing --------------------------------------------------------------

def test_parse_filters():
    assert parse_filters([]) == {}
    assert parse_filters(["garant:5", "garant:7", "category:x"]) == \
        {"garant": {5, 7}, "category": {"x"}}
    for bad in ("foo:1", "garant:x", "category", "attending:"):
        with pytest.raises(Invalid):
            parse_filters([bad])


def test_escape():
    assert _escape("a,b;c\nd\\e") == "a\\,b\\;c\\nd\\\\e"
    assert _escape("x\r\ny") == "x\\ny"


def test_fold_respects_utf8_boundaries():
    # 4-byte runes in the second line: naive byte cuts would split them
    for line in ("SUMMARY:" + "héllo, wörld! " * 12, "SUMMARY:" + "🔥" * 40):
        folded = _fold(line)
        assert all(len(part.encode()) <= 75 for part in folded.split("\r\n"))
        assert folded.replace("\r\n ", "") == line
