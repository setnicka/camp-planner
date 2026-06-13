"""Google Calendar sync state: the outbound op queue (outbox).

The per-camp connection itself (calendar id, sync token) lives as columns on Camp;
the per-slot event mapping (google_event_id) lives on Slot. This module only holds the
outbox: write paths stage a GoogleSyncOp on the session (like audit.record), and
`services.google_sync.drain` delivers them to Google out of band, so a slow or
unavailable Google never blocks (nor fails) a timeline edit.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from camp_planner.config import fk, table_name
from camp_planner.extensions import Base
from camp_planner.models.common import TimestampMixin, portable_enum

if TYPE_CHECKING:
    from camp_planner.models.camp import Camp


class SyncOpKind(str, enum.Enum):
    """What the outbox op asks Google to do with a slot's event."""

    upsert = "upsert"   # create the event (no event id yet) or patch it (has one)
    delete = "delete"   # remove the event (the slot is already gone)


class GoogleSyncOp(TimestampMixin, Base):
    """One pending outbound change for the drainer to push to Google Calendar.

    upsert ops carry slot_id (resolved to the live slot at drain time; a since-deleted
    slot makes the op a no-op). delete ops carry only google_event_id, since their slot
    no longer exists. Ops are processed oldest-first and removed on success; a failure
    bumps `attempts` / stores `last_error` and the row stays for the next drain.
    """

    __tablename__ = table_name("google_sync_ops")

    id: Mapped[int] = mapped_column(primary_key=True)
    camp_id: Mapped[int] = mapped_column(ForeignKey(fk("camps.id")), index=True)
    # ondelete CASCADE: a deleted slot's pending upsert is moot (on SQLite, where FKs
    # aren't enforced, drain skips ops whose slot has vanished, so it self-heals anyway).
    slot_id: Mapped[int | None] = mapped_column(
        ForeignKey(fk("slots.id"), ondelete="CASCADE"), nullable=True
    )
    google_event_id: Mapped[str | None] = mapped_column(String(255))
    op: Mapped[SyncOpKind] = mapped_column(portable_enum(SyncOpKind, "google_sync_op"))
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(Text)

    camp: Mapped[Camp] = relationship(back_populates="sync_ops")

    def __repr__(self) -> str:
        return f"<GoogleSyncOp {self.op.value} slot={self.slot_id} event={self.google_event_id!r}>"
