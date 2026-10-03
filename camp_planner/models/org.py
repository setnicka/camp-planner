"""Per-camp roster of orgs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from camp_planner.config import fk, table_name
from camp_planner.extensions import Base

if TYPE_CHECKING:
    from camp_planner.models.activity import ActivityAssignment, TodoAssignment
    from camp_planner.models.camp import Camp
    from camp_planner.models.material import MaterialAssignment
    from camp_planner.models.slot import SlotAssignment


class Org(Base):
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

    # Integration hook: an opaque id of this org in a host system, as a string so it holds
    # a numeric uid (as its decimal text) or a textual id. Nullable: not every org is linked.
    external_id: Mapped[str | None] = mapped_column(String(255), index=True)

    # Relationships:
    camp: Mapped[Camp] = relationship(back_populates="orgs")
    activity_assignments: Mapped[list[ActivityAssignment]] = relationship(
        back_populates="org", cascade="all, delete-orphan"
    )
    slot_assignments: Mapped[list[SlotAssignment]] = relationship(
        back_populates="org", cascade="all, delete-orphan"
    )
    todo_assignments: Mapped[list[TodoAssignment]] = relationship(
        back_populates="org", cascade="all, delete-orphan"
    )
    material_assignments: Mapped[list[MaterialAssignment]] = relationship(
        back_populates="org", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Org {self.initials!r}>"
