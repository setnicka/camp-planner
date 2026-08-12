"""Flask extension singletons, the shared declarative base, and the session proxy."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from flask import abort, current_app
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase
from werkzeug.local import LocalProxy

if TYPE_CHECKING:
    from sqlalchemy import Select
    from sqlalchemy.orm import Session


class Base(DeclarativeBase):
    """Shared SQLAlchemy 2.0 declarative base for all models."""


db = SQLAlchemy(model_class=Base)
migrate = Migrate()
# Initialized only on our own app (create_app); embedded hosts manage their own CSRF.
csrf = CSRFProtect()


def state() -> dict[str, Any]:
    """Our per-app wiring (integration._attach writes it); empty before it runs."""
    return current_app.extensions.get("camp_planner", {})


def _resolve_session() -> Session:
    """Ours, or an embedded host's if it passed one to register_camp_planner
    (docs/DEPLOYMENT.md §2). Per-app, so one process can host both.

    Always a Session, never a scoped_session: the two differ (no in_transaction on one,
    no remove on the other), so code would pass in one deployment and fail in the other.
    """
    injected = state().get("session")
    return injected() if injected is not None else db.session()


# Import this, never db.session. Named db_session because a bare `session` reads as
# the Flask one.
db_session: Session = LocalProxy(_resolve_session)  # type: ignore[assignment]


# flask-sqlalchemy's own get_or_404/first_or_404 are hardwired to its db.session.

def get_or_404(entity: type, ident: Any, *, description: str | None = None) -> Any:
    value = db_session.get(entity, ident)
    if value is None:
        abort(404, description=description)
    return value


def first_or_404(statement: Select, *, description: str | None = None) -> Any:
    value = db_session.execute(statement).scalar()
    if value is None:
        abort(404, description=description)
    return value


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
    """SQLite ignores foreign keys (so ON DELETE actions never fire) unless this
    pragma is set per connection. No-op on PostgreSQL/MySQL."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
