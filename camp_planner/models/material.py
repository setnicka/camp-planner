"""Material catalog (per-camp, deduplicated) and per-activity material usage."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from camp_planner.config import fk, table_name
from camp_planner.extensions import Base
from camp_planner.models.common import strip_diacritics

if TYPE_CHECKING:
    from camp_planner.models.activity import Activity
    from camp_planner.models.camp import Camp

_TOKEN_SPLIT = re.compile(r"[^0-9a-z]+")


class Material(Base):
    """A canonical material in the camp catalog (registry).

    normalized_name (lowercase, diacritics stripped, tokens sorted) is
    unique per camp, so "A4 papír" and "papír A4" cannot both become
    canonical entries. The frontend searches the camp's material list
    client-side and only creates a new one when nothing matches.
    """

    __tablename__ = table_name("materials")
    __table_args__ = (
        UniqueConstraint("camp_id", "normalized_name", name="uq_material_camp_norm"),
    )

    # Columns:
    id: Mapped[int] = mapped_column(primary_key=True)
    camp_id: Mapped[int] = mapped_column(ForeignKey(fk("camps.id")))  # index already part of the uq_material_camp_norm

    name: Mapped[str] = mapped_column(String(255))                  # canonical display name
    normalized_name: Mapped[str] = mapped_column(String(255), index=True)
    unit: Mapped[str | None] = mapped_column(String(40))           # default unit, e.g. "ks", "balení"
    note: Mapped[str | None] = mapped_column(Text)                  # optional catalog note
    url: Mapped[str | None] = mapped_column(String(1024))          # optional "where to buy" link

    # Relationships:
    camp: Mapped[Camp] = relationship(back_populates="materials")
    needs: Mapped[list[MaterialNeed]] = relationship(
        back_populates="material", cascade="all, delete-orphan"
    )

    @staticmethod
    def normalize_name(name: str) -> str:
        """Normalise a material name for duplicate detection: lowercase, strip
        diacritics, split into alphanumeric tokens, sort them. So "A4 papír",
        "papír A4" and "Papíry, A4" collapse to a comparable key, enforced
        unique per camp so the catalog can't hold normalized duplicates."""
        tokens = [t for t in _TOKEN_SPLIT.split(strip_diacritics(name.lower())) if t]
        return " ".join(sorted(tokens))

    @validates("name")
    def _sync_normalized(self, key: str, value: str) -> str:
        """Keep normalized_name in sync whenever name is set or changed."""
        self.normalized_name = self.normalize_name(value)
        return value


class MaterialNeed(Base):
    """How much of a catalog material one activity needs.

    amount + unit (numeric) drive the camp-wide shopping aggregation;
    note carries free text; is_ready marks it as sorted out for the activity.
    """

    __tablename__ = table_name("material_needs")
    __table_args__ = (
        UniqueConstraint("activity_id", "material_id", name="uq_material_need"),
    )

    # Columns:
    id: Mapped[int] = mapped_column(primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey(fk("activities.id")))  # index already part of the uq_material_need
    material_id: Mapped[int] = mapped_column(ForeignKey(fk("materials.id")), index=True)

    amount: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(40))           # overrides material.unit if set
    note: Mapped[str | None] = mapped_column(Text)
    is_ready: Mapped[bool] = mapped_column(default=False)

    # Relationships:
    activity: Mapped[Activity] = relationship(back_populates="material_needs")
    material: Mapped[Material] = relationship(back_populates="needs")
