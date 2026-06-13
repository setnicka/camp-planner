"""Pydantic models — the API's single source of truth for request validation and
the OpenAPI documentation.

Each request model validates the incoming body (spectree rejects a malformed one
with the pydantic error list at 422) and documents the endpoint; each response model
documents the returned shape and is built from the ORM by services/serialize.py.

Layering note: this imports only pydantic + the model *enums* (value source of
truth); it pulls in no service logic, so both the views and the services can depend
on it freely.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, NaiveDatetime, field_validator, model_validator

from camp_planner.models.activity import ActivityType, OrgRole
from camp_planner.models.audit import AuditAction, EntityType
from camp_planner.models.camp import TagKind
from camp_planner.models.slot import SlotRole

_TEXT_MAX = 100_000
_NOTE_MAX = 10_000


# --- shared ------------------------------------------------------------------

class _Ok(BaseModel):
    """Base for success envelopes — the constant ok=True flag."""
    ok: bool = True


class ErrorOut(BaseModel):
    """Base operational-error envelope (4xx). The per-code subclasses below only
    differ in their documented example message."""
    ok: bool = False
    error: str


class BadRequestOut(ErrorOut):
    """400 — a business rule failed (referenced row not in this camp, name clash, …)."""
    error: str = Field(examples=["Kategorie: neznámá kategorie této akce."])


class UnauthorizedOut(ErrorOut):
    """401 — not signed in."""
    error: str = Field(examples=["Pro přístup k této akci se přihlaste."])


class ForbiddenOut(ErrorOut):
    """403 — signed in but lacking the required role."""
    error: str = Field(examples=["K této akci nemáte oprávnění."])


class NotFoundOut(ErrorOut):
    """404 — no such entity."""
    error: str = Field(examples=["Akce nenalezena."])


class DeletedEnvelope(_Ok):
    id: int


# --- todos -------------------------------------------------------------------

class TodoCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255, examples=["Koupit lano"])
    note: str | None = Field(default=None, max_length=_NOTE_MAX)
    due_date: date | None = None
    is_done: bool = False


class TodoUpdate(BaseModel):
    """Partial update — only the fields actually present are applied."""
    title: str | None = Field(default=None, min_length=1, max_length=255)
    note: str | None = Field(default=None, max_length=_NOTE_MAX)
    due_date: date | None = None
    is_done: bool | None = None


class TodoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    activity_id: int
    title: str
    note: str | None
    due_date: date | None
    is_done: bool


class TodoEnvelope(_Ok):
    todo: TodoOut


class TodoWithActivityOut(TodoOut):
    """A todo plus its activity, for the camp-wide TODO overview."""
    activity_title: str


class TodoOverviewEnvelope(_Ok):
    todos: list[TodoWithActivityOut]


# --- slots -------------------------------------------------------------------

class SlotOrgsIn(BaseModel):
    """Replace the set of orgs attending a slot (PUT …/slots/<id>/orgs)."""
    org_ids: list[int] = []

    @model_validator(mode="after")
    def _unique(self):
        if len(set(self.org_ids)) != len(self.org_ids):
            raise ValueError("Orgové: org se v seznamu opakuje.")
        return self


class SlotOrgOut(BaseModel):
    org_id: int
    initials: str


class SlotOut(BaseModel):
    id: int
    activity_id: int
    role: SlotRole
    start_at: NaiveDatetime
    end_at: NaiveDatetime
    orgs: list[SlotOrgOut]


class SlotOrgsEnvelope(_Ok):
    orgs: list[SlotOrgOut]


# --- timeline ----------------------------------------------------------------

class _TimeSpan(BaseModel):
    """A naive-local [start_at, end_at) span; rejects end <= start."""
    start_at: NaiveDatetime
    end_at: NaiveDatetime

    @model_validator(mode="after")
    def _order(self):
        if self.end_at <= self.start_at:
            raise ValueError("Konec musí být po začátku.")
        return self


class MoveIn(_TimeSpan):
    slot_id: int


class RetypeIn(BaseModel):
    """Change an existing slot's role (main/prep/cleanup); placement is unaffected."""
    slot_id: int
    role: SlotRole


class TimelineCreate(_TimeSpan):
    """A new slot to add in a timeline batch (activity_id says which activity it joins)."""
    activity_id: int
    role: SlotRole = SlotRole.main


class TimelineSaveIn(BaseModel):
    """One atomic editing-session save: reposition (moves), add (creates) and remove
    (deletes) slots together, guarded by the optimistic-lock rev."""
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "rev": 7,
        "moves": [{"slot_id": 12, "start_at": "2026-07-04T14:00:00", "end_at": "2026-07-04T16:00:00"}],
        "creates": [{"activity_id": 4, "role": "prep",
                     "start_at": "2026-07-04T13:30:00", "end_at": "2026-07-04T14:00:00"}],
        "retypes": [{"slot_id": 9, "role": "cleanup"}],
        "deletes": [18],
    }]})
    rev: int | None = Field(default=None, description="Revize, kterou klient načetl (optimistický zámek).")
    moves: list[MoveIn] = []
    creates: list[TimelineCreate] = []
    retypes: list[RetypeIn] = []
    deletes: list[int] = []


class TimelineSaveEnvelope(_Ok):
    rev: int
    created: list[SlotOut]   # newly created slots, in the order of `creates` (for id mapping)


class TimelineCamp(BaseModel):
    slug: str
    name: str
    start_date: str
    length_days: int
    timezone: str
    window_start_min: int
    snap_minutes: int
    latitude: float | None
    longitude: float | None
    rev: int


class TimelineCategory(BaseModel):
    id: int
    key: str
    label: str
    color: str


class TimelineOrg(BaseModel):
    """A camp org; segments reference it by id (garants/helpers/attending)."""
    id: int
    initials: str
    name: str


class TimelineTag(BaseModel):
    """A camp tag; segments reference it by id (tag_ids)."""
    id: int
    name: str
    pinned: bool


class TimelineGroup(BaseModel):
    id: int             # day index (0-based); the segment's `day` joins to this
    iso_date: str       # the row's date; the frontend formats weekday + label


class TimelineSegment(BaseModel):
    slot_id: int
    activity_id: int
    day: int
    rel_start_min: int    # this segment's span within its day-row window (for layout)
    rel_end_min: int
    role: SlotRole
    cat_key: str
    title: str
    garants: list[int]    # org ids (resolve against payload.orgs); the activity's garants
    helpers: list[int]    # org ids; the activity's helpers
    attending: list[int]  # org ids; orgs attending this specific slot
    abs_start_min: int    # the whole slot's start/end in absolute camp-minutes (for the clock label)
    abs_end_min: int
    cont_back: bool
    cont_fwd: bool
    tag_ids: list[int]    # tag ids the activity carries (resolve against payload.tags)


class TimelinePayload(BaseModel):
    camp: TimelineCamp
    categories: list[TimelineCategory]
    orgs: list[TimelineOrg]
    tags: list[TimelineTag]
    groups: list[TimelineGroup]
    segments: list[TimelineSegment]


class TimelineOut(TimelinePayload, _Ok):
    pass


class ConflictOut(ErrorOut):
    """409 — an optimistic-lock race; carries the fresh state to reconcile against."""
    error: str = Field(examples=["Časový plán mezitím někdo změnil. Načtěte ho prosím znovu."])
    rev: int
    timeline: TimelinePayload


# --- activities --------------------------------------------------------------

class ActivityCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    category_id: int | None = None
    type: ActivityType = ActivityType.basic
    description_md: str | None = Field(default=None, max_length=_TEXT_MAX)
    config: dict | None = None


class ActivityUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    category_id: int | None = None
    type: ActivityType | None = None
    description_md: str | None = Field(default=None, max_length=_TEXT_MAX)
    config: dict | None = None


class AssignmentIn(BaseModel):
    org_id: int
    role: OrgRole


class ActivityOrgsIn(BaseModel):
    """Set the activity's garant/helper orgs (each org carries a role)."""
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"orgs": [{"org_id": 3, "role": "garant"}, {"org_id": 7, "role": "helper"}]},
    ]})
    orgs: list[AssignmentIn] = []

    @model_validator(mode="after")
    def _unique(self):
        keys = [(o.org_id, o.role) for o in self.orgs]
        if len(set(keys)) != len(keys):
            raise ValueError("Orgové: stejný org se opakuje ve stejné roli.")
        return self


class TagValueIn(BaseModel):
    tag_id: int
    value: str | None = Field(default=None, max_length=_TEXT_MAX)


class TagsIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"tags": [{"tag_id": 5, "value": "60"}, {"tag_id": 8, "value": None}]},
    ]})
    tags: list[TagValueIn] = []

    @model_validator(mode="after")
    def _unique(self):
        ids = [t.tag_id for t in self.tags]
        if len(set(ids)) != len(ids):
            raise ValueError("Tagy: stejný tag se opakuje.")
        return self


class AssignmentOut(BaseModel):
    org_id: int
    initials: str
    role: OrgRole


class TagLinkOut(BaseModel):
    tag_id: int
    name: str
    kind: TagKind
    pinned: bool
    value: str | None


class MaterialOut(BaseModel):
    """A catalog material (registry) — also returned by the catalog endpoints."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    unit: str | None
    note: str | None
    url: str | None


class MaterialNeedOut(BaseModel):
    """An activity's material need, with the catalog material nested under `material`."""
    id: int
    amount: float | None
    unit: str | None          # per-need override (None → fall back to material.unit)
    note: str | None          # how this activity uses the material
    is_ready: bool
    material: MaterialOut


class ActivityOut(BaseModel):
    id: int
    camp_id: int
    title: str
    type: ActivityType
    category_id: int | None
    description_md: str | None
    config: dict | None
    slots: list[SlotOut]
    orgs: list[AssignmentOut]
    tags: list[TagLinkOut]
    todos: list[TodoOut]
    material_needs: list[MaterialNeedOut]


class ActivityEnvelope(_Ok):
    activity: ActivityOut


class ActivityListEnvelope(_Ok):
    activities: list[ActivityOut]


class ActivityMergeIn(BaseModel):
    """Merge this activity INTO another: its todos/slots/needs move to `into`, this one is deleted."""
    into: int


class ActivityOrgsEnvelope(_Ok):
    orgs: list[AssignmentOut]


class TagsEnvelope(_Ok):
    tags: list[TagLinkOut]


class TagValueUpdate(BaseModel):
    """Update one applied tag's per-activity value (PATCH …/tags/<tag_id>). Validated
    against the tag's kind server-side: check → 'true'/'false', progress → '0'–'100',
    text → free, label → must be empty/null."""
    value: str | None = Field(default=None, max_length=_TEXT_MAX, examples=["60"])


class TagLinkEnvelope(_Ok):
    tag: TagLinkOut


# --- materials ---------------------------------------------------------------
# Catalog (Material): the per-camp registry. The frontend lists it and searches
# client-side; it creates a new one only when nothing matches, then links a usage.
# (MaterialOut / MaterialNeedOut are defined above, near ActivityOut.)

def _http_url(value: str | None) -> str | None:
    """A material's link must be an http(s) URL (or empty) — blocks javascript:/data:/etc.,
    which would otherwise execute when the rendered link is clicked. Blank clears it."""
    if not value:
        return None
    if not re.match(r"https?://", value, re.IGNORECASE):   # re.match is start-anchored
        raise ValueError("Odkaz musí začínat http:// nebo https://.")
    return value


class MaterialCreate(BaseModel):
    """Create a catalog material (registry). Deduplicated by normalized name per camp."""
    name: str = Field(min_length=1, max_length=255, examples=["A4 papír"])
    unit: str | None = Field(default=None, max_length=40, examples=["ks"])
    note: str | None = Field(default=None, max_length=_NOTE_MAX)
    url: str | None = Field(default=None, max_length=1024, examples=["https://example.com/a4"])

    _check_url = field_validator("url")(_http_url)


class MaterialUpdateIn(BaseModel):
    """Patch a catalog material; only the sent fields change. A rename is re-checked
    against the per-camp normalized-name uniqueness."""
    name: str | None = Field(default=None, min_length=1, max_length=255)
    unit: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=_NOTE_MAX)
    url: str | None = Field(default=None, max_length=1024)

    _check_url = field_validator("url")(_http_url)


class MaterialEnvelope(_Ok):
    material: MaterialOut


class MaterialListEnvelope(_Ok):
    materials: list[MaterialOut]


class MaterialUsageOut(BaseModel):
    """One activity's use of a catalog material, seen from the material side (for the
    camp-wide materials page). `unit` is the per-need override (None → material default)."""
    need_id: int
    activity_id: int
    activity_title: str
    amount: float | None
    unit: str | None
    note: str | None
    is_ready: bool


class MaterialWithUsagesOut(MaterialOut):
    """A catalog material plus every activity need that points at it."""
    usages: list[MaterialUsageOut]


class MaterialOverviewEnvelope(_Ok):
    materials: list[MaterialWithUsagesOut]


class MaterialMergeIn(BaseModel):
    """Merge this material INTO another: usages migrate to `into`, this one is deleted."""
    into: int


# Need (MaterialNeed): how much of a catalog material an activity needs.

class MaterialNeedAddIn(BaseModel):
    material_id: int
    amount: float | None = None
    unit: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=_NOTE_MAX)
    is_ready: bool = False


class MaterialNeedUpdateIn(BaseModel):
    amount: float | None = None
    unit: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=_NOTE_MAX)
    is_ready: bool | None = None


class MaterialNeedEnvelope(_Ok):
    need: MaterialNeedOut


# --- taxonomy (batch list reconcile) -----------------------------------------

class _TaxonomyIn(BaseModel):
    """Base for taxonomy item inputs: strip surrounding whitespace so min_length and
    uniqueness see the trimmed value — the reconcile service then trusts the fields."""
    model_config = ConfigDict(str_strip_whitespace=True)


class CategoryIn(_TaxonomyIn):
    id: int | None = None
    key: str | None = Field(default=None, max_length=40)
    label: str = Field(min_length=1, max_length=255)
    color: str | None = Field(default=None, max_length=7)


class OrgIn(_TaxonomyIn):
    id: int | None = None
    initials: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=255)


class TagDefIn(_TaxonomyIn):
    id: int | None = None
    name: str = Field(min_length=1, max_length=255)
    kind: TagKind = TagKind.label
    pinned: bool = False


class CategoryListIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"items": [{"key": "hra-fyzicka", "label": "Fyzická hra", "color": "#0b8043"}]},
    ]})
    items: list[CategoryIn] = []


class OrgListIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"items": [{"initials": "K", "name": "Karel"}]},
    ]})
    items: list[OrgIn] = []


class TagListIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"items": [{"name": "Hotovo", "kind": "check", "pinned": True}]},
    ]})
    items: list[TagDefIn] = []


class CategoryOut(BaseModel):
    id: int
    key: str
    label: str
    color: str


class OrgOut(BaseModel):
    id: int
    initials: str
    name: str


class TagDefOut(BaseModel):
    id: int
    name: str
    kind: TagKind
    pinned: bool


class TaxonomyEnvelope(_Ok):
    """Response of the batch-save PUTs (one kind per endpoint)."""
    items: list[dict]            # CategoryOut | OrgOut | TagDefOut (one kind per endpoint)
    message: str = "Uloženo."


# Per-collection read envelopes — symmetric with the per-collection PUTs.
class CategoriesEnvelope(_Ok):
    items: list[CategoryOut]


class OrgsEnvelope(_Ok):
    items: list[OrgOut]


class TagDefsEnvelope(_Ok):
    items: list[TagDefOut]


# --- camps -------------------------------------------------------------------

_SNAP = Literal[5, 10, 15, 30, 60]


class _TzValidated(BaseModel):
    """Mixin: validate the `timezone` field against the IANA database."""
    timezone: str = "Europe/Prague"

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"Neznámé časové pásmo {value!r}.") from None
        return value


class CampCreate(_TzValidated):
    name: str = Field(min_length=1, max_length=255)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9-]+$", max_length=80,
                             description="Nepovinné – odvodí se z názvu.")
    start_date: date
    length_days: int = Field(ge=1)
    window_start_min: int = Field(default=240, ge=0, le=1439)
    snap_minutes: _SNAP = 15
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    copy_from: str | None = Field(default=None, description="Slug akce, ze které převzít taxonomie.")
    copy_parts: list[Literal["categories", "orgs", "tags"]] | None = None


class CampUpdate(_TzValidated):
    """Full settings replace; name/slug are applied only for admins (server-side)."""
    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9-]+$", max_length=80)
    start_date: date
    length_days: int = Field(ge=1)
    window_start_min: int = Field(default=240, ge=0, le=1439)
    snap_minutes: _SNAP = 15
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class CampOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    start_date: date
    length_days: int
    timezone: str
    window_start_min: int
    snap_minutes: int
    latitude: float | None
    longitude: float | None
    timeline_rev: int


class CampEnvelope(_Ok):
    camp: CampOut


class CampListEnvelope(_Ok):
    camps: list[CampOut]


# --- audit log (read-only history) -------------------------------------------

class AuditEntryOut(BaseModel):
    """One recorded change. `changes` is a {field: [before, after]} diff (or null);
    `activity_id` groups slot/assignment/material edits under their activity."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    author: str | None
    action: AuditAction
    entity_type: EntityType
    entity_id: int | None
    activity_id: int | None
    changes: dict | None


class AuditQuery(BaseModel):
    """Filters for GET /camps/<slug>/audit. With no filter it's the whole-camp feed;
    activity_id narrows to one activity's thread, entity_type+entity_id to one row's
    history. `before` is a keyset cursor: pass the previous page's `next_before` to
    fetch older entries."""
    activity_id: int | None = None
    entity_type: EntityType | None = None
    entity_id: int | None = None
    before: int | None = Field(default=None, ge=1, description="Return entries older than this id.")
    limit: int = Field(default=100, ge=1, le=500)


class AuditEnvelope(_Ok):
    entries: list[AuditEntryOut]
    next_before: int | None = None   # cursor for the next (older) page; null → no more


# --- Google Calendar sync ----------------------------------------------------

class GoogleConnectIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, json_schema_extra={"examples": [
        {"calendar_id": "abc123@group.calendar.google.com"},
    ]})
    calendar_id: str = Field(
        min_length=1, max_length=255,
        description="ID kalendáře (Nastavení Google kalendáře → sekce „Integrace kalendáře“).")


class GoogleStatusOut(BaseModel):
    """Connection state for the settings panel. `enabled` is whether the deployment has a
    service account configured at all; `service_account_email` is the address a calendar
    must be shared with; `pending_ops` are outbound changes not yet pushed."""
    enabled: bool
    service_account_email: str | None
    calendar_id: str | None
    connected: bool
    pending_ops: int
    failed_ops: int = 0          # queued ops that have failed at least once
    last_error: str | None = None  # most recent push error (e.g. a read-only-share 403)


class GoogleEnvelope(_Ok):
    google: GoogleStatusOut


class GoogleSyncResultOut(BaseModel):
    pushed: int
    failed: int
    pending: int


class GoogleSyncEnvelope(_Ok):
    result: GoogleSyncResultOut
    google: GoogleStatusOut


class GooglePullPreviewEnvelope(_Ok):
    """The reviewable inbound diff. `changes` items carry a stable `key`, a `kind`
    (time_change | deleted_in_google | new_event), a Czech `label` and the relevant
    times; `activities`/`categories` are the options for importing a new event. `rev` is
    the timeline revision this was computed against — echo it back on apply."""
    rev: int
    changes: list[dict]
    activities: list[dict]
    categories: list[dict]


class GooglePullDecisionIn(BaseModel):
    key: str
    # apply = accept a time_change/deletion; new = import as a new activity; attach =
    # import onto target_activity_id. category_id is the new activity's category (optional).
    action: Literal["apply", "new", "attach"]
    target_activity_id: int | None = None
    category_id: int | None = None


class GooglePullApplyIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"rev": 7, "decisions": [{"key": "new:abc123", "action": "new", "category_id": 1}]},
    ]})
    rev: int | None = Field(default=None, description="timeline_rev from the preview; rejected (409) if stale.")
    decisions: list[GooglePullDecisionIn] = []


class GooglePullAppliedOut(BaseModel):
    created_activities: int
    imported_slots: int
    updated: int
    deleted: int


class GooglePullApplyEnvelope(_Ok):
    applied: GooglePullAppliedOut


class GooglePullConflictOut(ErrorOut):
    """409 — the timeline changed since the preview; re-pull. Carries the fresh rev (unlike
    the timeline's ConflictOut, the client just re-runs the pull rather than reconciling)."""
    error: str = Field(examples=["Časový plán se mezitím změnil. Načtěte změny z Google prosím znovu."])
    rev: int
