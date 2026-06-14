"""User-facing blueprint — camp list, the timeline (default camp view), and the
camp settings subpage with its taxonomy management."""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import available_timezones

from flask import Blueprint, flash, redirect, render_template, request, url_for

from camp_planner.auth.permissions import (
    can_edit,
    can_edit_camp_meta,
    can_view,
    require_admin,
    require_edit,
    require_view,
)
from camp_planner.extensions import db
from camp_planner.models.activity import Activity
from camp_planner.models.camp import Camp
from camp_planner.services import camps as camps_service
from camp_planner.services import errors as svc_errors
from camp_planner.services import loaders, serialize, taxonomy
from camp_planner.services.timeline import build_timeline

bp = Blueprint("main", __name__, template_folder="templates", static_folder="static")

# Timezone <select> choices, built once at import. Regional zones (Europe/…, …) in
# one group; intuitive fixed offsets ("GMT+1") in another — the latter map to the
# Etc/GMT∓N zone keys, whose POSIX sign is inverted, so we hide that confusion.
_TZ_ALL = available_timezones()
_TIMEZONES = sorted(z for z in _TZ_ALL if not z.startswith("Etc/"))
_TZ_FIXED = [
    (
        "GMT" if h == 0 else f"GMT{'+' if h > 0 else '-'}{abs(h)}",
        "UTC" if h == 0 else f"Etc/GMT{'-' if h > 0 else '+'}{abs(h)}",
    )
    for h in range(14, -13, -1)
]
_TZ_FIXED = [(label, key) for label, key in _TZ_FIXED if key in _TZ_ALL]
# Reverse map for display: zone key -> friendly label (e.g. "Etc/GMT-3" -> "GMT+3").
_TZ_LABELS = {key: label for label, key in _TZ_FIXED}

# Defaults shown on a fresh create form.
_NEW_CAMP_DEFAULTS = {
    "length_days": 7,
    "timezone": "Europe/Prague",
    "window_start_min": 4 * 60,
    "snap_minutes": 15,
}


def _camp_or_404(slug: str) -> Camp:
    return db.first_or_404(db.select(Camp).filter_by(slug=slug))


@bp.context_processor
def _form_choices() -> dict:
    """Expose the camp-form choice lists (snap-grid values, IANA timezones)."""
    return {"snap_choices": camps_service.SNAP_CHOICES, "timezones": _TIMEZONES, "tz_fixed": _TZ_FIXED}


@bp.get("/")
def index():
    camps = db.session.scalars(db.select(Camp).order_by(Camp.start_date.desc())).all()
    visible = [camp for camp in camps if can_view(camp)]
    return render_template("index.html", camps=visible)


def _copy_sources() -> list[Camp]:
    """Existing camps a new camp may copy its taxonomies from (those the user can view)."""
    camps = db.session.scalars(db.select(Camp).order_by(Camp.start_date)).all()
    return [camp for camp in camps if can_view(camp)]


@bp.get("/camps/new")
@require_admin
def camp_new():
    return render_template("camp_form.html", values=_NEW_CAMP_DEFAULTS, errors=[],
                           copy_sources=_copy_sources(), submitted=False)


@bp.post("/camps/new")
@require_admin
def camp_create():
    data, errors = camps_service.validate_camp_form(request.form)
    source = None
    source_slug = (request.form.get("copy_from") or "").strip()
    copy_parts = [p for p in taxonomy.COPY_PARTS if request.form.get(f"copy_{p}")]
    if source_slug:
        source = db.session.scalar(db.select(Camp).filter_by(slug=source_slug))
        if source is None or not can_view(source):
            errors.append("Převzít z akce: vyberte platnou akci.")
    if not errors:
        camp = camps_service.create_camp(data, copy_from=source, copy_parts=copy_parts)
        if camp is None:
            errors.append(f"Slug {data['slug']!r} už používá jiná akce.")
        else:
            flash(f"Akce „{camp.name}“ vytvořena.")
            return redirect(url_for("main.camp_timeline", slug=camp.slug))
    return render_template("camp_form.html", values=request.form, errors=errors,
                           copy_sources=_copy_sources(), submitted=True)


@bp.get("/camps/<slug>")
@require_view
def camp_timeline(slug: str):
    camp = db.first_or_404(db.select(Camp).filter_by(slug=slug).options(*loaders.TIMELINE))
    return render_template("camp_timeline.html", camp=camp, timeline=build_timeline(camp))


@bp.get("/camps/<slug>/detail")
@require_view
def camp_detail(slug: str):
    return _render_detail(_camp_or_404(slug))


# Czech labels for the activity type (read-only badge on the detail page).
_ACTIVITY_TYPE_LABELS = {
    "basic": "Vlastní program",
    "external": "Externí (odkaz)",
    "external_lecture": "Externí přednáška",
}


@bp.get("/camps/<slug>/activities/<int:activity_id>")
@require_view
def activity_detail(slug: str, activity_id: int):
    """One activity's page: orgs / tags / todos / material needs, all edited in place
    from the embedded JSON via the api endpoints (no reloads). Edit affordances are
    gated by can_edit; the api re-checks server-side."""
    camp = _camp_or_404(slug)
    activity = db.first_or_404(
        db.select(Activity).filter_by(id=activity_id, camp_id=camp.id).options(*loaders.ACTIVITY),
        description="Aktivita nenalezena.")
    tax = taxonomy.serialize(camp)
    aid = activity.id
    data = {
        "activity": serialize.activity(activity),
        "may_edit": can_edit(camp),
        "type_label": _ACTIVITY_TYPE_LABELS.get(activity.type.value, activity.type.value),
        # camp taxonomies, for the pickers
        "categories": tax["categories"],
        "orgs": tax["orgs"],
        "tag_defs": tax["tags"],
        "tag_kinds": list(taxonomy.TAG_KIND_LABELS.items()),
        # api endpoints. Item-scoped URLs (todoItem/needItem) carry a 0 sentinel the client
        # swaps for the real id; tag-value PATCH is just `tags`/<tag_id> built client-side.
        "urls": {
            "update": url_for("api.activity_update", activity_id=aid),
            "orgs": url_for("api.activity_orgs", activity_id=aid),
            "tags": url_for("api.activity_tags", activity_id=aid),
            "todoCreate": url_for("api.todo_create", activity_id=aid),
            "todoItem": url_for("api.todo_update", todo_id=0),
            "materialList": url_for("api.material_list", slug=camp.slug),
            "materialCreate": url_for("api.material_create", slug=camp.slug),
            "needCreate": url_for("api.material_need_add", activity_id=aid),
            "needItem": url_for("api.material_need_update", need_id=0),
            "materialsOverview": url_for("main.camp_materials", slug=camp.slug),
            "timeline": url_for("main.camp_timeline", slug=camp.slug),
            "slot": url_for("api.update_slot", slot_id=0),
        },
    }
    return render_template("activity_detail.html", camp=camp, activity=activity, data=data)


@bp.get("/camps/<slug>/materials")
@require_view
def camp_materials(slug: str):
    """Camp-wide materials overview: one row per catalog material with the activity needs
    that use it, edited in place from the embedded JSON via the api endpoints (no reloads).
    Edit affordances are gated by can_edit; the api re-checks server-side."""
    camp = db.first_or_404(
        db.select(Camp).filter_by(slug=slug).options(*loaders.MATERIALS_OVERVIEW),
        description="Akce nenalezena.")
    data = {
        "materials": [serialize.material_overview(m) for m in camp.materials],
        "may_edit": can_edit(camp),
        # api endpoints. Item-scoped URLs carry a 0 sentinel the client swaps for the real id;
        # materialItem serves both PATCH (edit) and DELETE, needItem both PATCH and DELETE.
        "urls": {
            "materialItem": url_for("api.material_update", slug=camp.slug, material_id=0),
            "materialMerge": url_for("api.material_merge", slug=camp.slug, source_id=0),
            "needItem": url_for("api.material_need_update", need_id=0),
            "activityDetail": url_for("main.activity_detail", slug=camp.slug, activity_id=0),
        },
    }
    return render_template("materials_overview.html", camp=camp, data=data)


@bp.get("/camps/<slug>/activities")
@require_view
def camp_overview(slug: str):
    """Camp-wide activity overview / status page: every activity in a filterable, sortable
    table — category, orgs, todo/material progress, a column per pinned tag, slot counts —
    with delete and merge from the embedded JSON via the api endpoints. Edit affordances are
    gated by can_edit; the api re-checks server-side."""
    camp = db.first_or_404(
        db.select(Camp).filter_by(slug=slug).options(*loaders.ACTIVITIES_OVERVIEW),
        description="Akce nenalezena.")
    tax = taxonomy.serialize(camp)   # categories / orgs (czech-sorted) / tags — reused as filter metadata
    data = {
        # order is decided client-side (the table re-sorts on every filter/sort change)
        "activities": [serialize.activity_overview(a) for a in camp.activities],
        # filter/column metadata: categories, orgs, and the pinned tags (= columns)
        "categories": [{"id": c["id"], "label": c["label"], "color": c["color"]} for c in tax["categories"]],
        "orgs": tax["orgs"],
        "pinned_tags": [{"id": t["id"], "name": t["name"], "kind": t["kind"]} for t in tax["tags"] if t["pinned"]],
        "may_edit": can_edit(camp),
        # activityItem serves DELETE; activityMerge is .../<id>/merge; the trailing 0 is a
        # sentinel the client swaps for the real id.
        "urls": {
            "activityItem": url_for("api.activity_delete", activity_id=0),
            "activityMerge": url_for("api.activity_merge", source_id=0),
            "activityDetail": url_for("main.activity_detail", slug=camp.slug, activity_id=0),
        },
    }
    return render_template("activities_overview.html", camp=camp, data=data)


@bp.get("/camps/<slug>/edit")
@require_edit
def camp_edit(slug: str):
    camp = _camp_or_404(slug)
    return render_template("camp_edit.html", camp=camp, values=_camp_values(camp), errors=[])


@bp.post("/camps/<slug>/edit")
@require_edit
def camp_edit_save(slug: str):
    camp = _camp_or_404(slug)
    allow_meta = can_edit_camp_meta(camp)
    data, errors = camps_service.validate_camp_form(request.form, require_meta=allow_meta)
    if errors:
        return render_template("camp_edit.html", camp=camp, values=request.form, errors=errors)
    try:
        camps_service.save_camp_settings(camp, data, allow_meta=allow_meta)
    except svc_errors.Invalid as exc:  # e.g. a date change clashing on a shared Google calendar
        return render_template("camp_edit.html", camp=camp, values=request.form, errors=[str(exc)])
    flash("Nastavení uloženo.")
    return redirect(url_for("main.camp_detail", slug=camp.slug))


def _camp_values(camp: Camp) -> dict:
    """Current scalar parameters, for pre-filling the edit form."""
    return {
        "name": camp.name,
        "slug": camp.slug,
        "start_date": camp.start_date.isoformat(),
        "length_days": camp.length_days,
        "timezone": camp.timezone,
        "window_start_min": camp.window_start_min,
        "snap_minutes": camp.snap_minutes,
        "latitude": "" if camp.latitude is None else camp.latitude,
        "longitude": "" if camp.longitude is None else camp.longitude,
    }


def _render_detail(camp: Camp):
    """Read-only camp detail: scalar parameters (edited on the separate /edit page)
    plus the taxonomy lists, which are edited in place from the embedded JSON and
    saved via the api PUT endpoints. (Copying taxonomies from another camp is only
    offered at creation, not here.)"""
    tax = taxonomy.serialize(camp)
    tax_data = {
        "may_edit": can_edit(camp),
        "tag_kinds": list(taxonomy.TAG_KIND_LABELS.items()),
        "urls": {
            "categories": url_for("api.categories_save", slug=camp.slug),
            "orgs": url_for("api.orgs_save", slug=camp.slug),
            "tags": url_for("api.tags_save", slug=camp.slug),
        },
        **tax,
    }
    # Google Calendar panel data — rendered server-side (no fetch on load), gated by
    # may_edit here and re-checked by the api. The status reports whether the feature is
    # configured at all (status.enabled), so the template can hide the whole section.
    google_data = {
        "may_edit": can_edit(camp),
        "status": camps_service.google_status(camp),
        "urls": {
            "base": url_for("api.google_status", slug=camp.slug),  # GET / PUT / DELETE
            "sync": url_for("api.google_sync_now", slug=camp.slug),
            "pull": url_for("api.google_pull_preview", slug=camp.slug),  # GET preview / POST apply
        },
    }
    return render_template(
        "camp_detail.html",
        camp=camp,
        end_date=camp.start_date + timedelta(days=max(0, camp.length_days - 1)),
        tz_label=_TZ_LABELS.get(camp.timezone, camp.timezone),
        tax_data=tax_data,
        google_data=google_data,
    )

# All taxonomy mutations (categories/orgs/tags batch save) live in the api blueprint;
# the detail page links to them via url_for("api.*"). Copying taxonomies from another
# camp is offered only on the create form (camp_create), never afterwards.
