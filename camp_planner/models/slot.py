"""Slots — the time spans of the activities + orgs mapping."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from camp_planner.config import fk, table_name
from camp_planner.extensions import Base
from camp_planner.models.common import portable_enum

if TYPE_CHECKING:
    from camp_planner.models.activity import Activity
    from camp_planner.models.org import Org


class SlotRole(str, enum.Enum):
    """A slot is the main block or an independently-placeable prep/cleanup margin."""

    main = "main"
    prep = "prep"
    cleanup = "cleanup"


class Slot(Base):
    """An independently-placeable time span belonging to an activity.

    An activity may have any number of slots of any role (main / prep / cleanup),
    including none. Times are naive datetimes interpreted in the camp's timezone.
    """

    __tablename__ = table_name("slots")
    __table_args__ = (CheckConstraint("end_at > start_at", name="ck_slot_time_order"),)

    # Columns:
    id: Mapped[int] = mapped_column(primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey(fk("activities.id")), index=True)
    role: Mapped[SlotRole] = mapped_column(portable_enum(SlotRole, "slot_role"), default=SlotRole.main)

    start_at: Mapped[datetime] = mapped_column(index=True)
    end_at: Mapped[datetime]

    # Optional display name shown on the timeline and used as the Google event title in
    # place of the activity title. Null (or empty) → fall back to activity.title.
    override_name: Mapped[str | None] = mapped_column(String(255))

    # Id of the mirroring Google Calendar event, once pushed (see services/google_sync.py).
    # Null until the slot has been synced; cleared if the camp is disconnected.
    google_event_id: Mapped[str | None] = mapped_column(String(255), index=True)

    # Relationships:
    activity: Mapped[Activity] = relationship(back_populates="slots")
    assignments: Mapped[list[SlotAssignment]] = relationship(
        back_populates="slot", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Slot {self.role.value} {self.start_at}–{self.end_at}>"


class SlotAssignment(Base):
    """Mapping of which orgs attend a particular time slot during the camp."""

    __tablename__ = table_name("slot_assignments")

    # Columns: the natural key (slot, org) is the primary key
    slot_id: Mapped[int] = mapped_column(ForeignKey(fk("slots.id")), primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey(fk("orgs.id")), primary_key=True, index=True)

    # Relationships:
    slot: Mapped[Slot] = relationship(back_populates="assignments")
    org: Mapped[Org] = relationship(back_populates="slot_assignments")
