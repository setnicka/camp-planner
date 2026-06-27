"""Shared SQLAlchemy eager-load option sets for the read paths.

The read endpoints (and the web timeline view) serialize whole object graphs —
activities → slots → attendees → org, needs → catalog material, and so on. Loading
those relationships lazily means one query per relationship per row (N+1); these
option sets pull each graph in a fixed handful of queries instead. Apply with
db.select(Model).options(*loaders.X).

Each set is scoped to exactly what its serializer walks, so an endpoint never
over-fetches a relationship it won't touch.
"""

from __future__ import annotations

from sqlalchemy.orm import selectinload

from camp_planner.models.activity import Activity, ActivityAssignment, ActivityTag, Todo, TodoAssignment
from camp_planner.models.camp import Camp
from camp_planner.models.material import Material, MaterialAssignment, MaterialNeed
from camp_planner.models.slot import Slot, SlotAssignment


def _activity_graph() -> tuple:
    """The graph serialize.activity() walks (slots+attendees, org roles, tag links,
    todos+their orgs, needs+catalog), as loader options rooted at Activity. A fresh tuple
    per call so the option objects can be reused standalone and nested under Camp.activities."""
    return (
        selectinload(Activity.slots).selectinload(Slot.assignments).selectinload(SlotAssignment.org),
        selectinload(Activity.assignments).selectinload(ActivityAssignment.org),
        selectinload(Activity.tags).selectinload(ActivityTag.tag),
        selectinload(Activity.todos).selectinload(Todo.assignments).selectinload(TodoAssignment.org),
        selectinload(Activity.material_needs).selectinload(MaterialNeed.material),
    )


# GET /activities/<id> — one activity's full detail graph.
ACTIVITY = _activity_graph()

# GET /camps/<slug>/activities — the same graph for every activity of the camp.
ACTIVITIES = (selectinload(Camp.activities).options(*_activity_graph()),)

# GET /camps/<slug>/timeline (+ web camp_timeline) — only what build_timeline reads:
# categories, and per activity its category, role assignments, tag links and placed
# slots with attendees. Notably NOT todos/needs (the timeline doesn't show them).
TIMELINE = (
    selectinload(Camp.categories),
    selectinload(Camp.activities).options(
        selectinload(Activity.category),
        selectinload(Activity.assignments).selectinload(ActivityAssignment.org),
        selectinload(Activity.tags),
        selectinload(Activity.slots).selectinload(Slot.assignments).selectinload(SlotAssignment.org),
    ),
)

# GET /camps/<slug>/materials — the catalog list; serialize.material walks each material's
# responsible orgs (the activity-detail picker reads it).
MATERIALS = (
    selectinload(Camp.materials).selectinload(Material.assignments).selectinload(MaterialAssignment.org),
)

# GET /camps/<slug>/materials/overview — each catalog material with its responsible orgs and
# its needs (+ the activity each need belongs to, for the activity_title column), plus the
# camp roster used to populate the edit modal's org picker.
MATERIALS_OVERVIEW = (
    selectinload(Camp.orgs),
    selectinload(Camp.materials).options(
        selectinload(Material.assignments).selectinload(MaterialAssignment.org),
        selectinload(Material.needs).selectinload(MaterialNeed.activity),
    ),
)

# GET /camps/<slug>/todos (+ web todos overview) — every activity's todos with their
# responsible orgs (each todo's .activity is its already loaded parent, so activity_title
# needs no further option), plus the camp roster used as filter metadata.
TODOS_OVERVIEW = (
    selectinload(Camp.orgs),
    selectinload(Camp.activities).selectinload(Activity.todos)
    .selectinload(Todo.assignments).selectinload(TodoAssignment.org),
)

# GET /camps/<slug>/activities (web overview/status page) — the camp's filter metadata
# (categories, orgs, tags) plus, per activity, what serialize.activity_overview counts:
# category, org assignments, tag links (value only — keyed by tag_id), slots (role only),
# todos (is_done) and needs (is_ready). Notably no slot attendees or need catalog rows.
ACTIVITIES_OVERVIEW = (
    selectinload(Camp.categories),
    selectinload(Camp.orgs),
    selectinload(Camp.tags),
    selectinload(Camp.activities).options(
        selectinload(Activity.category),
        selectinload(Activity.assignments).selectinload(ActivityAssignment.org),
        selectinload(Activity.tags),
        selectinload(Activity.slots),
        selectinload(Activity.todos),
        selectinload(Activity.material_needs),
    ),
)
