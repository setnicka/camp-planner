"""The warehouse (sklad): boxes, items with photos, and inventory checks.

Global, not camp-scoped: one warehouse per deployment, shared by every camp.

A check (inventura) is a set of observations, not a workflow with states: one
InventoryCheckRecord per item somebody actually looked at, holding what they saw. No
record means nobody checked it, and the items change only when the check is completed.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Float, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from camp_planner.config import fk, table_name
from camp_planner.extensions import Base
from camp_planner.models.common import TimestampMixin

if TYPE_CHECKING:
    from camp_planner.models.camp import Camp


class InventoryCheckStatus(str, enum.Enum):
    """A check is either being walked (`active`) or frozen history (`completed`). Read
    off active_lock, not stored (see InventoryCheck)."""

    active = "active"
    completed = "completed"


class InventoryBox(Base):
    """A box, or any other place a thing can sit.

    Places that are not really boxes ("Under the shelf", "Lost") are boxes with
    virtual=True.
    """

    __tablename__ = table_name("inventory_boxes")
    __table_args__ = (UniqueConstraint("name", name="uq_inventory_box_name"),)

    # Columns:
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))   # free text, e.g. "shelf A2"
    note: Mapped[str | None] = mapped_column(Text)
    # A place rather than a physical box; presentation only.
    virtual: Mapped[bool] = mapped_column(default=False)

    # Relationships: no delete cascade, an item only sits in its box. passive_deletes
    # leaves the items to the DB, which refuses to delete a box that still holds some;
    # the ORM would otherwise unfile them silently.
    items: Mapped[list[InventoryItem]] = relationship(back_populates="box", passive_deletes="all")


class InventoryItem(TimestampMixin, Base):
    """One thing in the warehouse, and how much of it there is.

    Duplicate names are allowed. count NULL means "we have it, the number is not the
    point" or "not counted"; a fuzzy quantity is written as the unit itself ("a lot of",
    "infinity"), so no separate qualifier is needed.

    Discarded things are kept, not deleted: discarded_at set, box_id cleared. Hard
    deletion is for mistakes only.
    """

    __tablename__ = table_name("inventory_items")

    # Columns:
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    # Extra names the item is also known by, for searching only.
    alt_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    url: Mapped[str | None] = mapped_column(String(1024))       # optional "where to buy" link
    note: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(40))        # NULL = pieces
    count: Mapped[float | None] = mapped_column(Float)         # float: 1.5 m, 0.5 kg
    # Nullable only because a discarded item has no box; a live item always has one.
    box_id: Mapped[int | None] = mapped_column(ForeignKey(fk("inventory_boxes.id")), index=True)
    discarded_at: Mapped[datetime | None] = mapped_column()

    # Relationships:
    box: Mapped[InventoryBox | None] = relationship(back_populates="items")
    photos: Mapped[list[InventoryPhoto]] = relationship(
        back_populates="item", cascade="all, delete-orphan",
        order_by="[InventoryPhoto.sort_order, InventoryPhoto.id]",
    )
    check_records: Mapped[list[InventoryCheckRecord]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<InventoryItem {self.name!r} box={self.box_id}>"


class InventoryPhoto(Base):
    """A photo of an item: one file per size variant under MEDIA_DIR, all named
    `filename`. The lowest sort_order is the item's title photo.
    """

    __tablename__ = table_name("inventory_photos")

    # Columns:
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey(fk("inventory_items.id"), ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(64))   # "<uuid4 hex>.jpg"
    sort_order: Mapped[int] = mapped_column(default=0)

    # Relationships:
    item: Mapped[InventoryItem] = relationship(back_populates="photos")


class InventoryCheck(Base):
    """One inventory check (inventura).

    active_lock is the check's state: 1 while it runs, NULL once it is done. Its unique
    index lets only one check be active, as every backend lets NULLs repeat in it; a
    partial unique index would not be portable.

    Cancelling an active check deletes it with its records; completing it freezes it as
    history. summary is filled in then and cannot be recomputed later, because completing
    the check overwrites the state its numbers compare against.
    """

    __tablename__ = table_name("inventory_checks")
    __table_args__ = (UniqueConstraint("active_lock", name="uq_inventory_check_active"),)

    # Columns:
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    active_lock: Mapped[int | None] = mapped_column(default=1)
    author: Mapped[str] = mapped_column(String(255))   # host-provided identity, as in AuditLog
    summary: Mapped[dict | None] = mapped_column(JSON)
    # Optional camp the check follows up on; the box page then shows what each linked
    # thing was taken for. SET NULL: the check outlives the camp.
    camp_id: Mapped[int | None] = mapped_column(
        ForeignKey(fk("camps.id"), ondelete="SET NULL", name="fk_inventory_check_camp")
    )
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column()

    # Relationships:
    records: Mapped[list[InventoryCheckRecord]] = relationship(
        back_populates="check", cascade="all, delete-orphan"
    )
    camp: Mapped[Camp | None] = relationship()

    @property
    def status(self) -> InventoryCheckStatus:
        active = self.active_lock is not None
        return InventoryCheckStatus.active if active else InventoryCheckStatus.completed

    def __repr__(self) -> str:
        return f"<InventoryCheck {self.name!r} {self.status.value}>"


class InventoryCheckRecord(Base):
    """What one person saw of one item during one check.

    The row's existence means "checked"; deleting it returns the item to unchecked.
    A non-discarded record is a complete snapshot, written into the item verbatim at
    completion, NULL count included: clearing a number is an observation, not "no
    change". box_id is where the thing actually is, so a move is this column differing
    from the item's. discarded=true means it is gone, and then count and unit must be NULL.

    Composite (check_id, item_id) key: one observation per item per check, so a second
    person looking at the same thing replaces the first answer rather than adding one.
    """

    __tablename__ = table_name("inventory_check_records")

    # Columns:
    check_id: Mapped[int] = mapped_column(
        ForeignKey(fk("inventory_checks.id"), ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[int] = mapped_column(
        ForeignKey(fk("inventory_items.id"), ondelete="CASCADE"), primary_key=True, index=True
    )

    discarded: Mapped[bool] = mapped_column(default=False)
    count: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(40))
    # SET NULL, not CASCADE: a deleted box must not take the observation with it.
    box_id: Mapped[int | None] = mapped_column(
        ForeignKey(fk("inventory_boxes.id"), ondelete="SET NULL"), index=True
    )
    # Snapshot of item.box_id when the record was created, never edited: a move is
    # from_box_id != box_id. NULL only on a discarded item, which had no box to come
    # from. Deliberately no FK: SET NULL on box deletion would erase the snapshot and
    # forge a "came back from discarded"; a dangling id just names a box that is gone.
    from_box_id: Mapped[int | None] = mapped_column()
    note: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(255))   # last writer of this observation
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())

    # Relationships:
    check: Mapped[InventoryCheck] = relationship(back_populates="records")
    item: Mapped[InventoryItem] = relationship(back_populates="check_records")
    box: Mapped[InventoryBox | None] = relationship()

    def __repr__(self) -> str:
        return f"<InventoryCheckRecord check={self.check_id} item={self.item_id}>"
