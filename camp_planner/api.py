"""JSON REST API blueprint (mounted at /api).

Thin transport over the service functions: resolve the entity, check the camp-scoped
permission, hand the (already validated) request body to the matching service.

Validation + docs: every endpoint is decorated with @spec.validate, so the request
body is validated against its pydantic schema (see schemas.py) and the endpoint is
documented in the OpenAPI spec / Swagger UI. The validated body arrives as a typed
model on request.context.json.

Error contract:
- malformed body → 422 with the pydantic error list (spectree, documented automatically);
- business rule (errors.Invalid) → 400 {ok, error};
- optimistic-lock race (errors.Conflict) → 409 {ok, error, rev, timeline};
- unauthenticated / forbidden / missing → 401 / 403 / 404 {ok, error} (via abort);
- mutations require the X-CSRFToken header (CSRFProtect, same as the web forms).
"""

from __future__ import annotations

from typing import Callable

from flask import Blueprint, abort, g, jsonify, request, url_for
from spectree import Response, SpecTree
from werkzeug.exceptions import HTTPException

from camp_planner.auth.permissions import can_create_camp, can_edit, can_edit_camp_meta, can_view
from camp_planner.extensions import db
from camp_planner.models.activity import Activity, Todo
from camp_planner.models.audit import EntityType
from camp_planner.models.camp import Camp
from camp_planner.models.material import Material, MaterialNeed
from camp_planner.models.slot import Slot
from camp_planner.schemas import (
    ActivityCreate,
    ActivityEnvelope,
    ActivityListEnvelope,
    ActivityMergeIn,
    ActivityOrgsEnvelope,
    ActivityOrgsIn,
    ActivityUpdate,
    AuditEnvelope,
    AuditQuery,
    CampCreate,
    CampEnvelope,
    CampListEnvelope,
    CampUpdate,
    BadRequestOut,
    CategoriesEnvelope,
    CategoryListIn,
    ConflictOut,
    DeletedEnvelope,
    ForbiddenOut,
    GoogleConnectIn,
    GoogleEnvelope,
    GooglePullApplyEnvelope,
    GooglePullApplyIn,
    GooglePullConflictOut,
    GooglePullPreviewEnvelope,
    GoogleResyncEnvelope,
    GoogleSyncEnvelope,
    MaterialCreate,
    MaterialEnvelope,
    MaterialListEnvelope,
    MaterialMergeIn,
    MaterialNeedAddIn,
    MaterialNeedEnvelope,
    MaterialNeedUpdateIn,
    MaterialOverviewEnvelope,
    MaterialUpdateIn,
    NotFoundOut,
    OrgListIn,
    OrgsEnvelope,
    SlotEnvelope,
    SlotUpdateIn,
    TagDefsEnvelope,
    TagLinkEnvelope,
    TagListIn,
    TagsEnvelope,
    TagsIn,
    TagValueUpdate,
    TaxonomyEnvelope,
    TimelineOut,
    TimelineSaveEnvelope,
    TimelineSaveIn,
    TodoCreate,
    TodoEnvelope,
    TodoOverviewEnvelope,
    TodoUpdate,
    UnauthorizedOut,
)
from camp_planner.services import activities
from camp_planner.services import camps as camps_service
from camp_planner.services import google_sync
from camp_planner.services import audit, errors, loaders, materials, serialize, slots, taxonomy, todos
from camp_planner.services.timeline import build_timeline
from camp_planner.version import __version__

bp = Blueprint("api", __name__, url_prefix="/api")

# OpenAPI generator: scans the spec.validate-decorated views to build the schema +
# Swagger UI (served at /apidoc/swagger, spec JSON at /apidoc/openapi.json) once
# spec.register(app) runs in create_app. A malformed body fails validation → 422.
#
# mode="strict": document ONLY routes decorated with @spec.validate (this API). The
#   default "normal" mode also sweeps in undecorated routes, which dumped the main
#   blueprint's HTML pages and /healthz into the spec under an untagged "default" group.
# naming_strategy: spectree defaults to "<Name>.<module-hash>" to disambiguate same-
#   named models across modules; ours are all unique in one module, so use plain names.
spec = SpecTree("flask", title="Camp Planner API", version=__version__,
                mode="strict",
                validation_error_status=422,
                naming_strategy=lambda model: model.__name__,
                nested_naming_strategy=lambda *names: ".".join(names))
# Documented error responses, each with a code-appropriate example message. _AUTH is
# the baseline every endpoint can hit (sign-in / permission / not-found); _AUTH_400
# adds the business-rule 400 for endpoints whose service can raise errors.Invalid.
_AUTH = {"HTTP_401": UnauthorizedOut, "HTTP_403": ForbiddenOut, "HTTP_404": NotFoundOut}
_AUTH_400 = {"HTTP_400": BadRequestOut, **_AUTH}


# --- error envelope ----------------------------------------------------------

@bp.errorhandler(HTTPException)
def _json_error(exc: HTTPException):
    """Render aborts (401/403/404/…) raised inside the API as JSON, not HTML."""
    return jsonify(ok=False, error=exc.description), exc.code or 500


# --- helpers -----------------------------------------------------------------

def _forbid(message: str) -> None:
    """Reject the request: 401 when not signed in, otherwise 403 with the message."""
    if not g.identity.is_authenticated:
        abort(401, "Pro přístup k této akci se přihlaste.")
    abort(403, message)


def _guard(camp: Camp, *, edit: bool) -> None:
    if not (can_edit(camp) if edit else can_view(camp)):
        _forbid("K této akci nemáte oprávnění.")


def _run(fn: Callable[[], dict]):
    """Call a service, mapping its outcome to the JSON envelope."""
    try:
        return jsonify(ok=True, **fn())
    except errors.Invalid as exc:
        return jsonify(ok=False, error=str(exc)), 400
    except errors.Conflict as exc:
        return jsonify(ok=False, error=str(exc), **exc.extra), 409


def _camp(slug: str, *options) -> Camp:
    """Resolve a camp by slug (404 if absent). Pass loaders.* options to eager-load the
    graph a read serializes, avoiding N+1; omit them for mutations that don't walk it."""
    return db.first_or_404(
        db.select(Camp).filter_by(slug=slug).options(*options), description="Akce nenalezena.")


def _activity(activity_id: int, *options) -> Activity:
    return db.first_or_404(
        db.select(Activity).filter_by(id=activity_id).options(*options),
        description="Aktivita nenalezena.")


def _slot(slot_id: int) -> Slot:
    return db.get_or_404(Slot, slot_id, description="Slot nenalezen.")


def _todo(todo_id: int) -> Todo:
    return db.get_or_404(Todo, todo_id, description="Úkol nenalezen.")


def _need(need_id: int) -> MaterialNeed:
    return db.get_or_404(MaterialNeed, need_id, description="Potřeba materiálu nenalezena.")


def _material(camp: Camp, material_id: int) -> Material:
    return db.first_or_404(
        db.select(Material).filter_by(id=material_id, camp_id=camp.id),
        description="Materiál nenalezen.")


# --- camps (list + read + create + edit scalar settings) ---------------------

@bp.get("/camps")
@spec.validate(resp=Response(HTTP_200=CampListEnvelope), tags=["camps"])
def camp_list():
    camps = db.session.scalars(db.select(Camp).order_by(Camp.start_date)).all()
    visible = [c for c in camps if can_view(c)]
    return _run(lambda: {"camps": [serialize.camp(c) for c in visible]})


@bp.get("/camps/<slug>")
@spec.validate(resp=Response(HTTP_200=CampEnvelope, **_AUTH), tags=["camps"])
def camp_get(slug: str):
    camp = _camp(slug)
    _guard(camp, edit=False)
    return _run(lambda: {"camp": serialize.camp(camp)})


@bp.post("/camps")
@spec.validate(json=CampCreate, resp=Response(HTTP_200=CampEnvelope, **_AUTH_400), tags=["camps"])
def camp_create():
    if not can_create_camp():
        _forbid("Vytvářet akce může jen administrátor.")
    payload = request.context.json

    def run():
        source = None
        if payload.copy_from:
            source = db.session.scalar(db.select(Camp).filter_by(slug=payload.copy_from))
            if source is None or not can_view(source):
                raise errors.Invalid("Převzít z akce: vyberte platnou akci.")
        data = payload.model_dump(exclude={"copy_from", "copy_parts"})
        data["slug"] = data["slug"] or camps_service.slugify(data["name"])
        camp = camps_service.create_camp(data, copy_from=source, copy_parts=payload.copy_parts)
        if camp is None:
            raise errors.Invalid(f"Slug {data['slug']!r} už používá jiná akce.")
        return {"camp": serialize.camp(camp)}

    return _run(run)


@bp.put("/camps/<slug>")
@spec.validate(json=CampUpdate, resp=Response(HTTP_200=CampEnvelope, **_AUTH), tags=["camps"])
def camp_update(slug: str):
    """Replace a camp's scalar settings. name/slug are applied only for admins
    (can_edit_camp_meta), never trusting the body."""
    camp = _camp(slug)
    _guard(camp, edit=True)
    allow_meta = can_edit_camp_meta(camp)
    data = request.context.json.model_dump(exclude_unset=True)

    def run():
        camps_service.save_camp_settings(camp, data, allow_meta=allow_meta)
        return {"camp": serialize.camp(camp)}

    return _run(run)


@bp.delete("/camps/<slug>")
@spec.validate(resp=Response(HTTP_200=DeletedEnvelope, **_AUTH_400), tags=["camps"])
def camp_delete(slug: str):
    """Delete a camp. Admin-only, and refused (400) while it still has activities."""
    camp = _camp(slug)
    if not can_edit_camp_meta(camp):
        _forbid("Mazat akce může jen administrátor.")
    return _run(lambda: camps_service.delete_camp(camp))


# --- Google Calendar sync (connect / disconnect / push) ----------------------

@bp.get("/camps/<slug>/google")
@spec.validate(resp=Response(HTTP_200=GoogleEnvelope, **_AUTH), tags=["google"])
def google_status(slug: str):
    """Current Google Calendar connection state for the camp."""
    camp = _camp(slug)
    _guard(camp, edit=True)
    return _run(lambda: {"google": camps_service.google_status(camp)})


@bp.put("/camps/<slug>/google")
@spec.validate(json=GoogleConnectIn, resp=Response(HTTP_200=GoogleEnvelope, **_AUTH_400), tags=["google"])
def google_connect(slug: str):
    """Connect the camp to a Google calendar by id (verifies access, queues an export)."""
    camp = _camp(slug)
    _guard(camp, edit=True)
    return _run(lambda: camps_service.set_google_calendar(camp, request.context.json.calendar_id))


@bp.delete("/camps/<slug>/google")
@spec.validate(resp=Response(HTTP_200=GoogleEnvelope, **_AUTH_400), tags=["google"])
def google_disconnect(slug: str):
    """Disconnect the camp from Google (leaves the events already in the calendar)."""
    camp = _camp(slug)
    _guard(camp, edit=True)
    return _run(lambda: camps_service.disconnect_google(camp))


@bp.post("/camps/<slug>/google/sync")
@spec.validate(resp=Response(HTTP_200=GoogleSyncEnvelope, **_AUTH_400), tags=["google"])
def google_sync_now(slug: str):
    """Deliver any queued outbound changes to Google now ("Synchronizovat nyní")."""
    camp = _camp(slug)
    _guard(camp, edit=True)
    return _run(lambda: {"result": google_sync.drain(camp), "google": camps_service.google_status(camp)})


@bp.post("/camps/<slug>/google/resync")
@spec.validate(resp=Response(HTTP_200=GoogleResyncEnvelope, **_AUTH_400), tags=["google"])
def google_resync(slug: str):
    """Queue every slot for an outbound push ("Znovu synchronizovat vše")."""
    camp = _camp(slug)
    _guard(camp, edit=True)
    return _run(lambda: {"result": google_sync.resync_all(camp), "google": camps_service.google_status(camp)})


@bp.get("/camps/<slug>/google/pull")
@spec.validate(resp=Response(HTTP_200=GooglePullPreviewEnvelope, **_AUTH_400), tags=["google"])
def google_pull_preview(slug: str):
    """Compute the reviewable list of changes made in Google ("Načíst změny z Google")."""
    camp = _camp(slug)
    _guard(camp, edit=True)
    return _run(lambda: google_sync.preview_pull(camp))


@bp.post("/camps/<slug>/google/pull")
@spec.validate(json=GooglePullApplyIn,
               resp=Response(HTTP_200=GooglePullApplyEnvelope, HTTP_409=GooglePullConflictOut, **_AUTH_400),
               tags=["google"])
def google_pull_apply(slug: str):
    """Apply the user-selected subset of inbound changes from the review screen. A stale
    `rev` (the timeline changed since the preview) yields 409."""
    camp = _camp(slug)
    _guard(camp, edit=True)
    payload = request.context.json
    return _run(lambda: google_sync.apply_pull(camp, payload.decisions, rev=payload.rev))


# --- taxonomy (batch list reconcile: PUT the whole desired list) -------------

def _save_taxonomy(camp: Camp, save_fn: Callable):
    """The items were shape-validated by the schema; the service does the cross-item
    reconcile (raising errors.Invalid on a duplicate or blocked delete)."""
    return _run(lambda: {"items": save_fn(camp, request.context.json.items), "message": "Uloženo."})


@bp.get("/camps/<slug>/categories")
@spec.validate(resp=Response(HTTP_200=CategoriesEnvelope, **_AUTH), tags=["taxonomy"])
def categories_get(slug: str):
    camp = _camp(slug)
    _guard(camp, edit=False)
    return _run(lambda: {"items": taxonomy.categories(camp)})


@bp.put("/camps/<slug>/categories")
@spec.validate(json=CategoryListIn, resp=Response(HTTP_200=TaxonomyEnvelope, **_AUTH_400), tags=["taxonomy"])
def categories_save(slug: str):
    camp = _camp(slug)
    _guard(camp, edit=True)
    return _save_taxonomy(camp, taxonomy.save_categories)


@bp.get("/camps/<slug>/orgs")
@spec.validate(resp=Response(HTTP_200=OrgsEnvelope, **_AUTH), tags=["taxonomy"])
def orgs_get(slug: str):
    camp = _camp(slug)
    _guard(camp, edit=False)
    return _run(lambda: {"items": taxonomy.orgs(camp)})


@bp.put("/camps/<slug>/orgs")
@spec.validate(json=OrgListIn, resp=Response(HTTP_200=TaxonomyEnvelope, **_AUTH_400), tags=["taxonomy"])
def orgs_save(slug: str):
    camp = _camp(slug)
    _guard(camp, edit=True)
    return _save_taxonomy(camp, taxonomy.save_orgs)


@bp.get("/camps/<slug>/tags")
@spec.validate(resp=Response(HTTP_200=TagDefsEnvelope, **_AUTH), tags=["taxonomy"])
def tags_get(slug: str):
    camp = _camp(slug)
    _guard(camp, edit=False)
    return _run(lambda: {"items": taxonomy.tags(camp)})


@bp.put("/camps/<slug>/tags")
@spec.validate(json=TagListIn, resp=Response(HTTP_200=TaxonomyEnvelope, **_AUTH_400), tags=["taxonomy"])
def tags_save(slug: str):
    camp = _camp(slug)
    _guard(camp, edit=True)
    return _save_taxonomy(camp, taxonomy.save_tags)


# --- timeline ----------------------------------------------------------------

@bp.get("/camps/<slug>/timeline")
@spec.validate(resp=Response(HTTP_200=TimelineOut, **_AUTH), tags=["timeline"])
def timeline_get(slug: str):
    camp = _camp(slug, *loaders.TIMELINE)
    _guard(camp, edit=False)
    return _run(lambda: build_timeline(camp))


@bp.patch("/camps/<slug>/timeline")
@spec.validate(json=TimelineSaveIn, resp=Response(HTTP_200=TimelineSaveEnvelope, HTTP_409=ConflictOut, **_AUTH_400),
               tags=["timeline"])
def timeline_save(slug: str):
    camp = _camp(slug)
    _guard(camp, edit=True)
    return _run(lambda: slots.save_timeline(camp, request.context.json))


# --- activities --------------------------------------------------------------

@bp.get("/camps/<slug>/activities")
@spec.validate(resp=Response(HTTP_200=ActivityListEnvelope, **_AUTH), tags=["activities"])
def activity_list(slug: str):
    camp = _camp(slug, *loaders.ACTIVITIES)
    _guard(camp, edit=False)
    return _run(lambda: {"activities": [serialize.activity(a) for a in camp.activities]})


@bp.get("/activities/<int:activity_id>")
@spec.validate(resp=Response(HTTP_200=ActivityEnvelope, **_AUTH), tags=["activities"])
def activity_get(activity_id: int):
    activity = _activity(activity_id, *loaders.ACTIVITY)
    _guard(activity.camp, edit=False)
    return _run(lambda: {"activity": serialize.activity(activity)})


@bp.post("/camps/<slug>/activities")
@spec.validate(json=ActivityCreate, resp=Response(HTTP_200=ActivityEnvelope, **_AUTH_400), tags=["activities"])
def activity_create(slug: str):
    camp = _camp(slug)
    _guard(camp, edit=True)
    return _run(lambda: activities.create_activity(camp, request.context.json))


@bp.patch("/activities/<int:activity_id>")
@spec.validate(json=ActivityUpdate, resp=Response(HTTP_200=ActivityEnvelope, **_AUTH_400), tags=["activities"])
def activity_update(activity_id: int):
    activity = _activity(activity_id, *loaders.ACTIVITY)
    _guard(activity.camp, edit=True)
    return _run(lambda: activities.update_activity(activity, request.context.json))


@bp.delete("/activities/<int:activity_id>")
@spec.validate(resp=Response(HTTP_200=DeletedEnvelope, **_AUTH_400), tags=["activities"])
def activity_delete(activity_id: int):
    """Delete an activity; refused (400) while it still has slots on the timeline."""
    activity = _activity(activity_id)
    _guard(activity.camp, edit=True)
    return _run(lambda: activities.delete_activity(activity))


@bp.post("/activities/<int:source_id>/merge")
@spec.validate(json=ActivityMergeIn, resp=Response(HTTP_200=ActivityEnvelope, **_AUTH_400), tags=["activities"])
def activity_merge(source_id: int):
    """Merge activity <source_id> INTO `into`: its todos/slots/needs move over, the source is
    deleted. Both must belong to the same camp."""
    source = _activity(source_id)
    _guard(source.camp, edit=True)
    target = _activity(request.context.json.into)
    return _run(lambda: activities.merge_activities(source, target))


@bp.put("/activities/<int:activity_id>/orgs")
@spec.validate(json=ActivityOrgsIn, resp=Response(HTTP_200=ActivityOrgsEnvelope, **_AUTH_400), tags=["activities"])
def activity_orgs(activity_id: int):
    """Set the activity's garant/helper orgs (each org carries a role)."""
    activity = _activity(activity_id)
    _guard(activity.camp, edit=True)
    return _run(lambda: activities.set_orgs(activity, request.context.json))


@bp.put("/activities/<int:activity_id>/tags")
@spec.validate(json=TagsIn, resp=Response(HTTP_200=TagsEnvelope, **_AUTH_400), tags=["activities"])
def activity_tags(activity_id: int):
    activity = _activity(activity_id)
    _guard(activity.camp, edit=True)
    return _run(lambda: activities.set_tags(activity, request.context.json))


@bp.patch("/activities/<int:activity_id>/tags/<int:tag_id>")
@spec.validate(json=TagValueUpdate, resp=Response(HTTP_200=TagLinkEnvelope, **_AUTH_400), tags=["activities"])
def activity_tag_value(activity_id: int, tag_id: int):
    """Update one applied tag's value (the part that changes over time); membership
    is set via PUT …/tags."""
    activity = _activity(activity_id)
    _guard(activity.camp, edit=True)
    link = next((t for t in activity.tags if t.tag_id == tag_id), None)
    if link is None:
        abort(404, "Tag není na této aktivitě použit.")
    return _run(lambda: activities.set_tag_value(link, request.context.json))


# --- slots -------------------------------------------------------------------
# Slot placement (add / reposition / remove) is edited only through the timeline
# batch (PATCH …/timeline); a slot's role is fixed at creation. The one slot-level
# endpoint patches its attendees and/or display-name override — neither is placement,
# so it's grouped under "timeline" too.

@bp.patch("/slots/<int:slot_id>")
@spec.validate(json=SlotUpdateIn, resp=Response(HTTP_200=SlotEnvelope, **_AUTH_400), tags=["timeline"])
def update_slot(slot_id: int):
    """Set which orgs attend this slot and/or its display-name override."""
    slot = _slot(slot_id)
    _guard(slot.activity.camp, edit=True)
    return _run(lambda: slots.update_slot(slot, request.context.json))


# --- materials ---------------------------------------------------------------
# Catalog (registry) lives on the camp; the frontend lists it and searches client-side.
# A usage (material-usage) links a catalog material to an activity.

@bp.get("/camps/<slug>/materials")
@spec.validate(resp=Response(HTTP_200=MaterialListEnvelope, **_AUTH), tags=["materials"])
def material_list(slug: str):
    camp = _camp(slug, *loaders.MATERIALS)
    _guard(camp, edit=False)
    return _run(lambda: materials.list_materials(camp))


@bp.get("/camps/<slug>/materials/overview")
@spec.validate(resp=Response(HTTP_200=MaterialOverviewEnvelope, **_AUTH), tags=["materials"])
def material_overview(slug: str):
    """Camp-wide materials page: every catalog material with the activity needs using it
    (the frontend computes per-unit sums)."""
    camp = _camp(slug, *loaders.MATERIALS_OVERVIEW)
    _guard(camp, edit=False)
    return _run(lambda: materials.list_materials_overview(camp))


@bp.post("/camps/<slug>/materials")
@spec.validate(json=MaterialCreate, resp=Response(HTTP_200=MaterialEnvelope, **_AUTH_400), tags=["materials"])
def material_create(slug: str):
    camp = _camp(slug)
    _guard(camp, edit=True)
    return _run(lambda: materials.create_material(camp, request.context.json))


@bp.patch("/camps/<slug>/materials/<int:material_id>")
@spec.validate(json=MaterialUpdateIn, resp=Response(HTTP_200=MaterialEnvelope, **_AUTH_400), tags=["materials"])
def material_update(slug: str, material_id: int):
    """Edit a catalog material (name / default unit / note / url)."""
    camp = _camp(slug)
    _guard(camp, edit=True)
    material = _material(camp, material_id)
    return _run(lambda: materials.update_material(material, request.context.json))


@bp.post("/camps/<slug>/materials/<int:source_id>/merge")
@spec.validate(json=MaterialMergeIn, resp=Response(HTTP_200=MaterialEnvelope, **_AUTH_400), tags=["materials"])
def material_merge(slug: str, source_id: int):
    """Merge the catalog material <source_id> INTO `into`: usages migrate, source is deleted."""
    camp = _camp(slug)
    _guard(camp, edit=True)
    source = _material(camp, source_id)
    target = _material(camp, request.context.json.into)
    return _run(lambda: materials.merge_materials(camp, source, target))


@bp.delete("/camps/<slug>/materials/<int:material_id>")
@spec.validate(resp=Response(HTTP_200=DeletedEnvelope, **_AUTH_400), tags=["materials"])
def material_delete(slug: str, material_id: int):
    """Delete a catalog material; refused (400) while activities still use it."""
    camp = _camp(slug)
    _guard(camp, edit=True)
    material = _material(camp, material_id)
    return _run(lambda: materials.delete_material(material))


@bp.post("/activities/<int:activity_id>/materials")
@spec.validate(json=MaterialNeedAddIn, resp=Response(HTTP_200=MaterialNeedEnvelope, **_AUTH_400), tags=["materials"])
def material_need_add(activity_id: int):
    activity = _activity(activity_id)
    _guard(activity.camp, edit=True)
    return _run(lambda: materials.add_need(activity, request.context.json))


@bp.patch("/material-needs/<int:need_id>")
@spec.validate(json=MaterialNeedUpdateIn, resp=Response(HTTP_200=MaterialNeedEnvelope, **_AUTH), tags=["materials"])
def material_need_update(need_id: int):
    need = _need(need_id)
    _guard(need.activity.camp, edit=True)
    return _run(lambda: materials.update_need(need, request.context.json))


@bp.delete("/material-needs/<int:need_id>")
@spec.validate(resp=Response(HTTP_200=DeletedEnvelope, **_AUTH), tags=["materials"])
def material_need_delete(need_id: int):
    need = _need(need_id)
    _guard(need.activity.camp, edit=True)
    return _run(lambda: materials.delete_need(need))


# --- todos -------------------------------------------------------------------

@bp.get("/camps/<slug>/todos")
@spec.validate(resp=Response(HTTP_200=TodoOverviewEnvelope, **_AUTH), tags=["todos"])
def todo_overview(slug: str):
    """Camp-wide TODO overview: every activity's todos, each carrying its activity."""
    camp = _camp(slug, *loaders.TODOS_OVERVIEW)
    _guard(camp, edit=False)
    return _run(lambda: todos.list_todos_overview(camp))


@bp.post("/activities/<int:activity_id>/todos")
@spec.validate(json=TodoCreate, resp=Response(HTTP_200=TodoEnvelope, **_AUTH), tags=["todos"])
def todo_create(activity_id: int):
    activity = _activity(activity_id)
    _guard(activity.camp, edit=True)
    return _run(lambda: todos.create_todo(activity, request.context.json))


@bp.patch("/todos/<int:todo_id>")
@spec.validate(json=TodoUpdate, resp=Response(HTTP_200=TodoEnvelope, **_AUTH), tags=["todos"])
def todo_update(todo_id: int):
    todo = _todo(todo_id)
    _guard(todo.activity.camp, edit=True)
    return _run(lambda: todos.update_todo(todo, request.context.json))


@bp.delete("/todos/<int:todo_id>")
@spec.validate(resp=Response(HTTP_200=DeletedEnvelope, **_AUTH), tags=["todos"])
def todo_delete(todo_id: int):
    todo = _todo(todo_id)
    _guard(todo.activity.camp, edit=True)
    return _run(lambda: todos.delete_todo(todo))


# --- audit log (read-only history) -------------------------------------------

@bp.get("/camps/<slug>/audit")
@spec.validate(query=AuditQuery, resp=Response(HTTP_200=AuditEnvelope, **_AUTH), tags=["audit"])
def audit_list(slug: str):
    """Camp change history, newest first. No filter → whole-camp feed; activity_id →
    one activity's thread; entity_type+entity_id → one row's history; camp_level →
    high-level structural changes only."""
    camp = _camp(slug)
    _guard(camp, edit=False)
    q = request.context.query

    def run():
        res = audit.list_audit(
            camp, activity_id=q.activity_id, entity_type=q.entity_type,
            entity_id=q.entity_id, camp_level=q.camp_level, before=q.before, limit=q.limit)
        # Link activity/material entries to their pages — only on cross-entity (whole-camp)
        # feeds; within one activity's or one row's thread the target never varies.
        if q.activity_id is None and q.entity_id is None:
            _link_audit_entities(camp, res["entries"])
        return res

    return _run(run)


def _link_audit_entities(camp: Camp, entries: list[dict]) -> None:
    """Name + link entities on a whole-camp feed (deleted targets stay a plain generic noun):
      • entity_title/entity_url — the entry's own activity/material when it still exists, so
        the headline shows its name (and the merge target's) instead of a generic noun;
      • activity_title/activity_url — the parent activity of a per-activity detail entry
        (slot/todo/…), shown as a context line.
    Two batched lookups total (activity titles cover both uses; material names)."""
    act, mat = EntityType.activity.value, EntityType.material.value
    activity_ids = {e["activity_id"] for e in entries if e["activity_id"]}
    activity_ids |= {e["entity_id"] for e in entries if e["entity_type"] == act and e["entity_id"]}
    titles = audit.activity_titles(camp, activity_ids)
    mat_names = audit.material_names(camp, {e["entity_id"] for e in entries
                                            if e["entity_type"] == mat and e["entity_id"]})

    for e in entries:
        if e["entity_type"] == act and e["entity_id"] in titles:
            e["entity_title"] = titles[e["entity_id"]]
            e["entity_url"] = url_for("main.activity_detail", slug=camp.slug, activity_id=e["entity_id"])
        elif e["entity_type"] == mat and e["entity_id"] in mat_names:
            e["entity_title"] = mat_names[e["entity_id"]]
            e["entity_url"] = url_for("main.camp_materials", slug=camp.slug)
        # Detail entries (slot/todo/material_need/…) get their parent activity as a context line.
        if e["entity_type"] != act and e["activity_id"] in titles:
            e["activity_title"] = titles[e["activity_id"]]
            e["activity_url"] = url_for("main.activity_detail", slug=camp.slug, activity_id=e["activity_id"])
