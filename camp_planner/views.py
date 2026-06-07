"""User-facing blueprint — the camp / timeline views grow here."""

from __future__ import annotations

from flask import Blueprint, render_template

from camp_planner.auth.permissions import can_view
from camp_planner.extensions import db
from camp_planner.models.camp import Camp

bp = Blueprint("main", __name__, template_folder="templates", static_folder="static")


@bp.get("/")
def index():
    camps = db.session.scalars(db.select(Camp).order_by(Camp.start_date)).all()
    visible = [camp for camp in camps if can_view(camp)]
    return render_template("index.html", camps=visible)
