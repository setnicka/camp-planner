"""CLI commands: init-db, create-user, grant-role. (sync-google's drain logic is
covered by test_google_sync.py; the command is a thin loop over it.)"""

from __future__ import annotations

from camp_planner.extensions import db
from camp_planner.models.auth import User


def test_init_db_creates_schema(app):
    res = app.test_cli_runner().invoke(args=["init-db"])
    assert res.exit_code == 0
    assert "Schema created" in res.output


def test_create_user_with_grants(app, seeded):
    res = app.test_cli_runner().invoke(args=[
        "create-user", "franta", "--display-name", "Franta Novák",
        "--grant", f"editor:{seeded['camp_id']}", "--grant", "viewer:*",
        "--password", "tajne"])
    assert res.exit_code == 0, res.output

    user = db.session.scalar(db.select(User).filter_by(username="franta"))
    assert user.display_name == "Franta Novák" and not user.is_admin
    assert user.check_password("tajne") and not user.check_password("spatne")
    grants = {(g.role.value, g.camp_id) for g in user.camp_roles}
    assert grants == {("editor", seeded["camp_id"]), ("viewer", None)}


def test_create_user_duplicate_aborts(app):
    runner = app.test_cli_runner()
    runner.invoke(args=["create-user", "franta", "--password", "x"])
    res = runner.invoke(args=["create-user", "franta", "--password", "y"])
    assert "already exists" in res.output
    assert db.session.scalar(db.select(db.func.count()).select_from(User)) == 1


def test_create_user_rejects_bad_grant(app):
    runner = app.test_cli_runner()
    res = runner.invoke(args=["create-user", "franta", "--grant", "boss:*", "--password", "x"])
    assert res.exit_code != 0 and "unknown role" in res.output
    res = runner.invoke(args=["create-user", "franta", "--grant", "editor:99999",
                              "--password", "x"])
    assert res.exit_code != 0 and "no camp with id" in res.output
    assert db.session.scalar(db.select(User).filter_by(username="franta")) is None


def test_grant_role_flow(app, seeded):
    runner = app.test_cli_runner()
    runner.invoke(args=["create-user", "franta", "--password", "x"])

    res = runner.invoke(args=["grant-role", "franta", "editor:abc"])
    assert res.exit_code != 0 and "scope must be" in res.output

    res = runner.invoke(args=["grant-role", "franta", f"editor:{seeded['camp_id']}"])
    assert res.exit_code == 0 and "Granted" in res.output
    res = runner.invoke(args=["grant-role", "franta", f"editor:{seeded['camp_id']}"])
    assert "already has" in res.output          # duplicate grant refused

    res = runner.invoke(args=["grant-role", "neexistuje", "viewer:*"])
    assert "No such user" in res.output
