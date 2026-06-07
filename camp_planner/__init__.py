"""Application factory."""

from __future__ import annotations

import os

from flask import Flask

from camp_planner.integration import register_camp_planner, wire_app
from camp_planner.cli import register_cli
from camp_planner.config import config_by_name
from camp_planner.extensions import csrf, db, migrate

__all__ = ["create_app", "register_camp_planner"]


def create_app(config_name: str | None = None) -> Flask:
    # Env (incl. .env) is loaded by the entry point (wsgi.py), not here.
    config_cls = config_by_name.get(
        config_name or os.environ.get("FLASK_ENV", "development"),
        config_by_name["development"],
    )

    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)
    app.config.from_object(config_cls)
    config_cls.init_app(app)

    db.init_app(app)
    # render_as_batch lets Alembic rebuild tables on SQLite (which can't ALTER columns),
    # keeping migrations portable.
    migrate.init_app(app, db, render_as_batch=True)
    csrf.init_app(app)

    # Import models so they're registered on the metadata (migrations / create_all).
    from camp_planner import models  # noqa: F401

    wire_app(app)
    register_cli(app)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
