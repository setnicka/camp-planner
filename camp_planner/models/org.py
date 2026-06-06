"""Per-camp roster of orgs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from camp_planner.config import fk, table_name
from camp_planner.extensions import Base
from camp_planner.models.common import ExternalIdMixin

if TYPE_CHECKING:
    from camp_planner.models.activity import ActivityAssignment
    from camp_planner.models.camp import Camp
    from camp_planner.models.slot import SlotAssignment


class Org(ExternalIdMixin, Base):
    """Organizer, a person who can be assigned to activities, configured per camp.

    Displayed compactly as initials on the timeline (e.g. K,B,O) and by
    full name on detail pages.
    """

    __tablename__ = table_name("orgs")
    __table_args__ = (UniqueConstraint("camp_id", "initials", name="uq_org_camp_initials"),)

    # Columns:
    id: Mapped[int] = mapped_column(primary_key=True)
    camp_id: Mapped[int] = mapped_column(ForeignKey(fk("camps.id")))  # index already part of the uq_org_camp_initials

    name: Mapped[str] = mapped_column(String(255))
    initials: Mapped[str] = mapped_column(String(16))

    # Relationships:
    camp: Mapped[Camp] = relationship(back_populates="orgs")
    activity_assignments: Mapped[list[ActivityAssignment]] = relationship(
        back_populates="org", cascade="all, delete-orphan"
    )
    slot_assignments: Mapped[list[SlotAssignment]] = relationship(
        back_populates="org", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Org {self.initials!r}>"
