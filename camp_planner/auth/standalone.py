"""Standalone auth: the app's own users, sessions, login/logout, and admin user
management.

Active only when AUTH_MODE=standalone. Identity comes from the Flask session;
users and their per-camp role grants live in the users / user_camp_roles tables.
Admins can create/delete users in-app (/auth/users); the CLI (see cli.py) also
manages users and role grants. There is no self-registration, email, or
password-reset flow.
"""

from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from camp_planner.auth.identity import ALL, ANONYMOUS, CampRole, Identity, build_identity
from camp_planner.auth.permissions import require_admin
from camp_planner.extensions import db
from camp_planner.models.auth import User, UserCampRole
from camp_planner.models.camp import Camp

# A throwaway hash compared against when a username is unknown, so a failed login
# costs the same whether or not the user exists (defeats username enumeration by timing).
_DUMMY_HASH = generate_password_hash("dummy-password-for-constant-time-login")


class StandaloneProvider:
    def load_identity(self) -> Identity:
        user_id = session.get("user_id")
        if user_id is None:
            return ANONYMOUS
        user = db.session.get(User, user_id)
        if user is None:                       # stale session (user deleted)
            session.pop("user_id", None)
            return ANONYMOUS
        raw_grants = [
            (grant.role, ALL if grant.camp_id is None else frozenset({grant.camp_id}))
            for grant in user.camp_roles
        ]
        return build_identity(
            user_id=user.username,             # username is what AuditLog records
            display_name=user.display_name,
            is_admin=user.is_admin,
            raw_grants=raw_grants,
        )


bp = Blueprint("auth", __name__, url_prefix="/auth")


def _safe_next(target: str | None) -> str | None:
    """Only allow same-site, root-relative redirect targets (no open redirects)."""
    if not target:
        return None
    # Browsers fold backslashes to slashes, so normalize before parsing —
    # otherwise "/\evil.com" slips past urlparse as a path, then redirects off-site.
    parsed = urlparse(target.replace("\\", "/"))
    if parsed.scheme or parsed.netloc or not target.startswith("/"):
        return None
    return target


def _is_self(user: User) -> bool:
    """Whether `user` is the logged-in account (used to guard against self-lockout)."""
    return user.id == session.get("user_id")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        user = db.session.scalar(
            db.select(User).filter_by(username=request.form.get("username", ""))
        )
        if user and user.check_password(password):
            session["user_id"] = user.id
            return redirect(_safe_next(request.form.get("next")) or url_for("main.index"))
        if user is None:
            check_password_hash(_DUMMY_HASH, password)  # equalize timing vs. a real check
        flash("Neplatné uživatelské jméno nebo heslo.")
    return render_template("auth/login.html", next=request.values.get("next", ""))


@bp.post("/logout")
def logout():
    session.pop("user_id", None)
    return redirect(url_for("auth.login"))


@bp.get("/users")
@require_admin
def users():
    rows = db.session.scalars(db.select(User).order_by(User.username)).all()
    camp_slugs = {camp.id: camp.slug for camp in db.session.scalars(db.select(Camp)).all()}
    return render_template(
        "auth/users.html",
        users=rows,
        camp_slugs=camp_slugs,
        current_user_id=session.get("user_id"),
    )


@bp.post("/users")
@require_admin
def create_user():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    display_name = request.form.get("display_name", "").strip()
    is_admin = bool(request.form.get("is_admin"))
    if not username or not password:
        flash("Uživatelské jméno a heslo jsou povinné.")
    elif db.session.scalar(db.select(User).filter_by(username=username)):
        flash(f"Uživatel {username!r} už existuje.")
    else:
        user = User(username=username, display_name=display_name or username, is_admin=is_admin)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash(f"Uživatel {username!r} byl vytvořen.")
    return redirect(url_for("auth.users"))


@bp.post("/users/<int:user_id>/delete")
@require_admin
def delete_user(user_id: int):
    user = db.get_or_404(User, user_id)
    if _is_self(user):
        # Refusing self-deletion also guarantees at least one admin always survives.
        flash("Nemůžete smazat vlastní účet.")
    else:
        db.session.delete(user)
        db.session.commit()
        flash(f"Uživatel {user.username!r} byl smazán.")
    return redirect(url_for("auth.users"))


@bp.get("/users/<int:user_id>")
@require_admin
def user_detail(user_id: int):
    user = db.get_or_404(User, user_id)
    camps = db.session.scalars(db.select(Camp).order_by(Camp.start_date)).all()
    return render_template(
        "auth/user_detail.html",
        user=user,
        camps=camps,
        camp_slugs={camp.id: camp.slug for camp in camps},
        roles=[role.value for role in CampRole],
        is_self=_is_self(user),
    )


@bp.post("/users/<int:user_id>/profile")
@require_admin
def update_profile(user_id: int):
    user = db.get_or_404(User, user_id)
    username = request.form.get("username", "").strip()
    display_name = request.form.get("display_name", "").strip()
    password = request.form.get("password", "")
    # Admins can't strip their own admin rights (avoids self-lockout); the checkbox
    # is disabled for self in the form, so a disabled (= unsubmitted) box stays on.
    make_admin = True if _is_self(user) else bool(request.form.get("is_admin"))

    clash = db.session.scalar(db.select(User).filter_by(username=username)) if username else None
    if not username or not display_name:
        flash("Uživatelské jméno a zobrazované jméno jsou povinné.")
    elif clash is not None and clash.id != user.id:
        flash(f"Uživatelské jméno {username!r} je už obsazené.")
    else:
        user.username = username
        user.display_name = display_name
        user.is_admin = make_admin
        if password:
            user.set_password(password)
        db.session.commit()
        flash(f"Uživatel {user.username!r} byl upraven.")
    return redirect(url_for("auth.user_detail", user_id=user.id))


@bp.post("/users/<int:user_id>/grants")
@require_admin
def add_grant(user_id: int):
    user = db.get_or_404(User, user_id)
    try:
        role = CampRole(request.form.get("role", ""))
    except ValueError:
        flash("Neznámá role.")
        return redirect(url_for("auth.user_detail", user_id=user.id))
    camp_raw = request.form.get("camp_id", "").strip()
    camp_id = int(camp_raw) if camp_raw else None       # blank = all camps
    if camp_id is not None and db.session.get(Camp, camp_id) is None:
        flash(f"Akce s id {camp_id} neexistuje.")
    elif db.session.scalar(
        db.select(UserCampRole).filter_by(user_id=user.id, camp_id=camp_id, role=role)
    ):
        flash("Toto oprávnění už existuje.")
    else:
        db.session.add(UserCampRole(user_id=user.id, camp_id=camp_id, role=role))
        db.session.commit()
        flash("Oprávnění bylo přidáno.")
    return redirect(url_for("auth.user_detail", user_id=user.id))


@bp.post("/users/<int:user_id>/grants/<int:grant_id>/delete")
@require_admin
def remove_grant(user_id: int, grant_id: int):
    grant = db.get_or_404(UserCampRole, grant_id)
    if grant.user_id != user_id:
        abort(404)
    db.session.delete(grant)
    db.session.commit()
    flash("Oprávnění bylo odebráno.")
    return redirect(url_for("auth.user_detail", user_id=user_id))
