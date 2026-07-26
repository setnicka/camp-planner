"""Shared org-assignment replacement (todos, materials, slots). Activities keep
their own role-aware variant with a per-role diff."""

from __future__ import annotations

from camp_planner.models.common import czech_sort_key
from camp_planner.services import errors


def replace_assignments(owner, camp, org_ids: list[int], assignment_cls) -> list | None:
    """Replace `owner.assignments` with `assignment_cls` rows for `org_ids` (validated
    against the camp roster). Returns the [before, after] audit diff (czech-sorted
    initials), or None when unchanged — then nothing is written."""
    initials = {o.id: o.initials for o in camp.orgs}
    for oid in org_ids:
        if oid not in initials:
            raise errors.Invalid("Orgové: neznámý org této akce.")
    before = sorted((a.org.initials for a in owner.assignments), key=czech_sort_key)
    after = sorted((initials[oid] for oid in org_ids), key=czech_sort_key)
    if before == after:  # initials are unique per camp
        return None
    owner.assignments = [assignment_cls(org_id=oid) for oid in org_ids]
    return [before, after]
