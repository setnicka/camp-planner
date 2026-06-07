"""Application config and database-backend selection.

The schema is DB-agnostic (plain SQLAlchemy types), so the same code runs on
SQLite, PostgreSQL or MySQL. TABLE_PREFIX is read at import time (table names
are fixed when the models import, so it can't live in app.config); everything
else is applied to app.config by the Config classes in create_app.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.engine import URL

if TYPE_CHECKING:
    from flask import Flask

# Optional prefix on every table name so the schema can share a DB with other
# apps. Read before the models import; applied via table_name()/fk(). Empty = none.
TABLE_PREFIX = os.environ.get("DB_TABLE_PREFIX", "")


def table_name(name: str) -> str:
    """Apply the configured table prefix: table_name("camps") -> "<prefix>camps"."""
    return f"{TABLE_PREFIX}{name}"


# A foreign-key target is prefixed identically ("camps.id" -> "<prefix>camps.id")
fk = table_name


def _build_database_url(instance_path: str) -> str:
    """Build the SQLAlchemy URL from the env: DATABASE_URL if set, else
    DB_BACKEND (sqlite | postgresql | mysql) assembled from the DB_* parts."""
    explicit = os.environ.get("DATABASE_URL")
    if explicit:
        return explicit

    backend = os.environ.get("DB_BACKEND", "sqlite").lower()

    if backend == "sqlite":
        sqlite_path = os.environ.get("SQLITE_PATH", "camp_planner.sqlite")
        path = Path(sqlite_path)
        if not path.is_absolute():
            path = Path(instance_path) / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path}"

    # Install the matching extra: camp-planner[postgres] / [mysql].
    drivers = {"postgresql": "postgresql+psycopg", "mysql": "mysql+pymysql"}
    if backend not in drivers:
        raise ValueError(
            f"Unknown DB_BACKEND={backend!r}; expected sqlite, postgresql or mysql"
        )

    port = os.environ.get("DB_PORT")
    return URL.create(
        drivers[backend],
        username=os.environ.get("DB_USER"),
        password=os.environ.get("DB_PASSWORD"),
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(port) if port else None,  # None → driver default (5432 / 3306)
        database=os.environ.get("DB_NAME", "camp_planner"),
    ).render_as_string(hide_password=False)


class Config:
    """Base config, populated from the environment."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-change-me")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # pool_pre_ping avoids stale-connection errors (esp. MySQL).
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # "standalone" (own users + login) or "proxy" (X-Remote-* headers); embedded ignores it.
    AUTH_MODE = os.environ.get("AUTH_MODE", "standalone")
    # Local-dev stand-in for the proxy, e.g. {"user_id": "dev", "roles": "admin"}.
    DEV_USER: dict | None = None
    # Base template every page extends; an embedding host can override it.
    BASE_TEMPLATE = os.environ.get("BASE_TEMPLATE", "_layouts/full.html")

    @staticmethod
    def init_app(app: Flask) -> None:
        app.config["SQLALCHEMY_DATABASE_URI"] = _build_database_url(app.instance_path)


class DevelopmentConfig(Config):
    DEBUG = True
    # Reload templates on edit and don't cache static files, regardless of how the
    # app is launched (so template/CSS changes show on refresh without a restart).
    TEMPLATES_AUTO_RELOAD = True
    SEND_FILE_MAX_AGE_DEFAULT = 0


class ProductionConfig(Config):
    DEBUG = False


class TestingConfig(Config):
    TESTING = True

    @staticmethod
    def init_app(app: Flask) -> None:
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
