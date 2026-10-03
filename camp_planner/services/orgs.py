"""Org-assignment replacement: a flat set (todos, materials, slots) or an activity's
garant/helper roles. Neither commits; the audit diff names czech-sorted initials."""

from __future__ import annotations

from camp_planner.models.activity import ActivityAssignment, OrgRole
from camp_planner.models.common import czech_sort_key
from camp_planner.services import errors


def _roster(camp, org_ids) -> dict[int, str]:
    """{org id: initials} of the camp; refuses an id that isn't on it."""
    initials = {o.id: o.initials for o in camp.orgs}
    if any(oid not in initials for oid in org_ids):
        raise errors.Invalid("Orgové: neznámý org této akce.")
    return initials


def _sorted(initials) -> list[str]:
    return sorted(initials, key=czech_sort_key)


def replace_assignments(owner, camp, org_ids: list[int], assignment_cls) -> dict[str, list]:
    """Replace `owner.assignments` with `assignment_cls` rows for `org_ids`. Returns
    {"orgs": [before, after]}, or {} when unchanged (then nothing is written)."""
    initials = _roster(camp, org_ids)
    before = _sorted(a.org.initials for a in owner.assignments)
    after = _sorted(initials[oid] for oid in org_ids)
    if before == after:  # initials are unique per camp
        return {}
    owner.assignments = [assignment_cls(org_id=oid) for oid in org_ids]
    return {"orgs": [before, after]}


def replace_roles(activity, pairs: list[tuple[int, OrgRole]]) -> dict[str, list]:
    """Replace the activity's (org id, role) assignments. Returns the diff of the roles
    that changed, or {} when none did (then nothing is written, no delete-orphan churn)."""
    initials = _roster(activity.camp, [oid for oid, _ in pairs])
    current = [(a.org_id, a.role) for a in activity.assignments]
    changes: dict[str, list] = {}
    for role in OrgRole:
        before = _sorted(initials[oid] for oid, r in current if r == role)
        after = _sorted(initials[oid] for oid, r in pairs if r == role)
        if before != after:
            changes[role.value] = [before, after]
    if changes:
        activity.assignments = [ActivityAssignment(org_id=oid, role=role) for oid, role in pairs]
    return changes
