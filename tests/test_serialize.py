"""Tests for the ORM → response-model adapters, and for the relationship ordering they lean on.

The pages render these lists as-is, so their order is part of the contract: unordered, the
database may hand back a different row order after an edit and silently reshuffle the UI.
"""

from __future__ import annotations

from datetime import datetime

from camp_planner.extensions import db
from camp_planner.models.activity import Activity, ActivityAssignment, ActivityTag, OrgRole
from camp_planner.models.camp import Tag, TagKind
from camp_planner.models.material import Material, MaterialNeed
from camp_planner.models.org import Org
from camp_planner.models.slot import Slot, SlotRole
from camp_planner.services import serialize


def test_activity_overview_serializes_slots(app, seeded):
    """activity_overview exposes every slot (role + span + override_name), time-ordered, so the
    overview can derive per-role counts and the chronological sort's main-slot rows client-side."""
    a = db.session.get(Activity, seeded["activity_id"])
    db.session.add_all([   # main + prep slots added out of order — Activity.slots time-orders them
        Slot(activity_id=a.id, role=SlotRole.main, override_name="Odpolední",
             start_at=datetime(2026, 7, 5, 14, 0), end_at=datetime(2026, 7, 5, 15, 0)),
        Slot(activity_id=a.id, role=SlotRole.main,
             start_at=datetime(2026, 7, 5, 9, 0), end_at=datetime(2026, 7, 5, 10, 0)),
        Slot(activity_id=a.id, role=SlotRole.prep, override_name="Příprava",
             start_at=datetime(2026, 7, 5, 8, 0), end_at=datetime(2026, 7, 5, 8, 30)),
    ])
    db.session.commit()

    out = serialize.activity_overview(a)
    # all roles, time-ordered (prep 08:00 first), each with span + override_name
    assert [(s["role"], s["start_at"]) for s in out["slots"]] == [
        ("prep", "2026-07-05T08:00:00"),
        ("main", "2026-07-05T09:00:00"),
        ("main", "2026-07-05T14:00:00"),
    ]
    main = [s for s in out["slots"] if s["role"] == "main"]
    assert main[0]["override_name"] is None
    assert main[1]["override_name"] == "Odpolední"
    assert main[1]["end_at"] == "2026-07-05T15:00:00"


def test_activity_slots_relationship_is_time_ordered(app, seeded):
    """Activity.slots is ordered by the same keys as the timeline's `order` comparator, so every
    consumer sees one sequence whatever order the rows were written in. Unordered, the DB may
    return them differently after an edit and reshuffle how overlapping slots stack."""
    a = db.session.get(Activity, seeded["activity_id"])
    db.session.add_all([   # written out of order
        Slot(activity_id=a.id, role=SlotRole.main,
             start_at=datetime(2026, 7, 5, s), end_at=datetime(2026, 7, 5, e))
        for s, e in [(20, 21), (8, 9), (14, 15), (8, 12)]
    ])
    db.session.commit()
    db.session.expire_all()   # force a reload, so this reads the relationship's ORDER BY

    a = db.session.get(Activity, seeded["activity_id"])
    # 08:00–12:00 before 08:00–09:00: equal starts put the longer slot first (bottom lane)
    assert [(s.start_at.hour, s.end_at.hour) for s in a.slots] == [(8, 12), (8, 9), (14, 15), (20, 21)]


def test_activity_lists_are_deterministically_ordered(app, seeded):
    """serialize.activity() sorts the relationships that carry no SQL order — the detail page
    renders them as-is. Orgs and materials are Czech-collated (Č next to C, not after Z as its
    code point would put it), tags follow the curated Tag.sort_order."""
    camp_id, a_id = seeded["camp_id"], seeded["activity_id"]
    orgs = [Org(camp_id=camp_id, name=n, initials=n[0]) for n in ("Dana", "Čeněk", "Adam")]
    tags = [Tag(camp_id=camp_id, name=n, kind=TagKind.label, sort_order=o)
            for n, o in [("třetí", 3), ("druhý", 2)]]          # linked in reverse below
    mats = [Material(camp_id=camp_id, name=n) for n in ("Žula", "Čep", "Deska")]
    db.session.add_all(orgs + tags + mats)
    db.session.flush()
    db.session.add_all(
        [ActivityAssignment(activity_id=a_id, org_id=o.id, role=OrgRole.garant) for o in orgs]
        + [ActivityTag(activity_id=a_id, tag_id=t.id) for t in tags]
        + [MaterialNeed(activity_id=a_id, material_id=m.id, amount=1) for m in mats])
    db.session.commit()
    db.session.expire_all()

    out = serialize.activity(db.session.get(Activity, a_id))
    assert [o["initials"] for o in out["orgs"]] == ["A", "Č", "D"]
    assert [t["name"] for t in out["tags"]] == ["druhý", "třetí"]
    assert [n["material"]["name"] for n in out["material_needs"]] == ["Čep", "Deska", "Žula"]
