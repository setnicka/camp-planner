"""Shared model building blocks: the portable-enum helper and column mixins.

Per-model enums live next to the model that uses them (e.g. SlotRole in
slot.py); this module holds only what's shared across models.
"""

from __future__ import annotations

import enum
import re
import unicodedata
from datetime import datetime

from sqlalchemy import Enum, String, func
from sqlalchemy.orm import Mapped, mapped_column


def strip_diacritics(text: str) -> str:
    """Drop accents/diacritics via NFKD decomposition."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Lowercase, strip diacritics, collapse non-alphanumeric runs to single
    hyphens, trim. Capped at the slug columns' 80 chars."""
    return _SLUG_STRIP.sub("-", strip_diacritics(name.lower())).strip("-")[:80]


def czech_sort_key(text: str):
    """Sort key approximating Czech collation: diacritics are folded so accented
    letters sort next to their base (á≈a, č≈c) instead of after 'z' (their Unicode
    code-point position), with the original casefolded text as a tiebreaker so a/á
    stay deterministically ordered. Not full ICU collation (e.g. 'ch' isn't one
    letter), but right for short names/initials and dependency-free."""
    return (strip_diacritics(text).casefold(), text.casefold())


def portable_enum(enum_cls: type[enum.Enum], name: str) -> Enum:
    """Build a SQLAlchemy Enum stored as a CHECK-constrained string rather than
    a native DB ENUM, so schema/migrations behave the same on SQLite,
    PostgreSQL and MySQL."""
    return Enum(enum_cls, name=name, native_enum=False, validate_strings=True)


class TimestampMixin:
    """created_at / updated_at columns maintained by the DB."""

    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())


class ExternalIdMixin:
    """An opaque id of this record in a host system, for integration.

    Stored as a string so it holds either a numeric uid (as its decimal text)
    or a textual id, depending on the host system this deployment integrates
    with. Nullable: not every record is linked. Indexed for lookups by it.
    """

    external_id: Mapped[str | None] = mapped_column(String(255), index=True)


