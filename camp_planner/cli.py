"""Flask CLI: schema bootstrap, standalone-user management.
"""

from __future__ import annotations

import re
from pathlib import Path

import click
from flask import Flask, current_app
from sqlalchemy import make_url

from camp_planner.auth.identity import CampRole
from camp_planner.extensions import db


_OBJECT_RE = re.compile(r"\{[^{}]*\}")
_FIELD_RE = re.compile(r"(\w+)\s*:\s*'((?:[^'\\]|\\.)*)'")
# initials tokens: letters (incl. Czech) of length 1–4, ignoring punctuation/parens
_ORG_TOKEN_RE = re.compile(r"[A-Za-zÁ-ž]{1,4}")


def _parse_events(data_js: Path) -> list[dict]:
    """Extract the EVENTS array entries from the mockup data.js."""
    text = data_js.read_text(encoding="utf-8")
    start = text.index("const EVENTS")
    end = text.index("];", start)
    block = text[start:end]
    events = []
    for obj in _OBJECT_RE.findall(block):
        fields = {k: v for k, v in _FIELD_RE.findall(obj)}
        if "start" in fields and "end" in fields:
            events.append(fields)
    return events


def _parse_org_tokens(orgs: str) -> list[str]:
    """Initials tokens, deduplicated, in first-seen order."""
    return list(dict.fromkeys(_ORG_TOKEN_RE.findall(orgs or "")))


def _parse_grant(token: str) -> tuple[CampRole, int | None]:
    """Parse a role:scope grant token, e.g. editor:12 or viewer:*.

    Returns (role, camp_id) where camp_id is None for an unscoped (*)
    grant. Validates the role against the CampRole enum.
    """
    name, _, scope = token.partition(":")
    try:
        role = CampRole(name)
    except ValueError as exc:
        raise click.BadParameter(
            f"unknown role {name!r}; expected one of {[r.value for r in CampRole]}"
        ) from exc
    if scope in ("", "*"):
        return role, None
    if not scope.isdigit():
        raise click.BadParameter(f"scope must be a camp id or '*', got {scope!r}")
    return role, int(scope)


def _require_camp(camp_id: int | None) -> None:
    """Friendly check that a scoped grant points at an existing camp (the FK
    enforces this too, but raising here avoids an ugly IntegrityError)."""
    if camp_id is None:
        return
    from camp_planner.models.camp import Camp

    if db.session.get(Camp, camp_id) is None:
        raise click.BadParameter(f"no camp with id {camp_id}")


def register_cli(app: Flask) -> None:
    @app.cli.command("init-db")
    def init_db() -> None:
        """Create all tables directly (quick start; prefer migrations otherwise)."""
        db.create_all()
        uri = make_url(current_app.config["SQLALCHEMY_DATABASE_URI"])
        click.echo(f"Schema created on {uri.render_as_string(hide_password=True)}")

    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--display-name", default=None, help="Shown in the UI; defaults to username.")
    @click.option("--admin", is_flag=True, help="Grant the global admin role.")
    @click.option(
        "--grant", "grants", multiple=True, metavar="ROLE:SCOPE",
        help="Repeatable per-camp grant, e.g. editor:12 or viewer:* (all camps).",
    )
    @click.password_option()
    def create_user(
        username: str, display_name: str | None, admin: bool,
        grants: tuple[str, ...], password: str,
    ) -> None:
        """Create a standalone-auth user (with optional role grants)."""
        from camp_planner.models.auth import User, UserCampRole

        if db.session.scalar(db.select(User).filter_by(username=username)):
            click.echo(f"User {username!r} already exists — aborting.")
            return
        user = User(username=username, display_name=display_name or username, is_admin=admin)
        user.set_password(password)
        parsed = [_parse_grant(g) for g in grants]
        for _, camp_id in parsed:
            _require_camp(camp_id)
        db.session.add(user)
        db.session.flush()
        for role, camp_id in parsed:
            db.session.add(UserCampRole(user_id=user.id, camp_id=camp_id, role=role))
        db.session.commit()
        scope = "admin" if admin else (", ".join(grants) or "no grants")
        click.echo(f"Created user {username!r} ({scope}).")

    @app.cli.command("grant-role")
    @click.argument("username")
    @click.argument("grant", metavar="ROLE:SCOPE")
    def grant_role(username: str, grant: str) -> None:
        """Add a per-camp grant to an existing user, e.g. editor:12 or viewer:*."""
        from camp_planner.models.auth import User, UserCampRole

        user = db.session.scalar(db.select(User).filter_by(username=username))
        if user is None:
            click.echo(f"No such user {username!r}.")
            return
        role, camp_id = _parse_grant(grant)
        _require_camp(camp_id)
        exists = db.session.scalar(
            db.select(UserCampRole).filter_by(user_id=user.id, camp_id=camp_id, role=role)
        )
        if exists:
            click.echo(f"{username!r} already has {grant}.")
            return
        db.session.add(UserCampRole(user_id=user.id, camp_id=camp_id, role=role))
        db.session.commit()
        click.echo(f"Granted {grant} to {username!r}.")
