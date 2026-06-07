"""Flask extension singletons and the shared declarative base."""

from __future__ import annotations

import sqlite3

from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared SQLAlchemy 2.0 declarative base for all models."""


db = SQLAlchemy(model_class=Base)
migrate = Migrate()
# Initialized only on our own app (create_app); embedded hosts manage their own CSRF.
csrf = CSRFProtect()


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
    """SQLite ignores foreign keys (so ON DELETE actions never fire) unless this
    pragma is set per connection. No-op on PostgreSQL/MySQL."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
