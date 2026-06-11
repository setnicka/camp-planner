"""Service-layer error signals: exceptions for conditions the pydantic schemas can't
express, which the API layer maps to HTTP status codes.
"""

from __future__ import annotations


class Invalid(ValueError):
    """A business rule failed (e.g. a referenced row isn't in this camp). → HTTP 400."""


class Conflict(Exception):
    """The change raced another edit. → HTTP 409. extra is merged into the JSON
    response so the client can recover (e.g. the fresh timeline + rev)."""

    def __init__(self, message: str, **extra: object) -> None:
        super().__init__(message)
        self.extra = extra
