"""Service-layer error signals: exceptions for conditions the pydantic schemas can't
express, which the API layer maps to HTTP status codes.
"""

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy.exc import IntegrityError

from camp_planner.extensions import db_session


class Invalid(ValueError):
    """A business rule failed (e.g. a referenced row isn't in this camp). → HTTP 400."""


class Conflict(Exception):
    """The change raced another edit. → HTTP 409. extra is merged into the JSON
    response so the client can recover (e.g. the fresh timeline + rev)."""

    def __init__(self, message: str, **extra: object) -> None:
        super().__init__(message)
        self.extra = extra


@contextmanager
def unique_or_invalid(message: str):
    """Wrap a flush/commit: a unique-constraint violation becomes Invalid(message). The
    failed flush leaves the session unusable, so this is the one place that rolls back."""
    try:
        yield
    except IntegrityError:
        db_session.rollback()
        raise Invalid(message) from None
