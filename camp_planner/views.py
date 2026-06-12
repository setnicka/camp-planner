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
from camp_planner.models.camp import Camp
from camp_planner.services import camps as camps_service
from camp_planner.services import loaders, taxonomy
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
    camps = db.session.scalars(db.select(Camp).order_by(Camp.start_date)).all()
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
    camps_service.save_camp_settings(camp, data, allow_meta=allow_meta)
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
    return render_template(
        "camp_detail.html",
        camp=camp,
        end_date=camp.start_date + timedelta(days=max(0, camp.length_days - 1)),
        tz_label=_TZ_LABELS.get(camp.timezone, camp.timezone),
        tax_data=tax_data,
    )

# All taxonomy mutations (categories/orgs/tags batch save) live in the api blueprint;
# the detail page links to them via url_for("api.*"). Copying taxonomies from another
# camp is offered only on the create form (camp_create), never afterwards.
