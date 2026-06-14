"""Camp and its per-camp taxonomies (category palette, tags)."""

from __future__ import annotations

import enum
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.ext.associationproxy import AssociationProxy, association_proxy
from sqlalchemy.orm import Mapped, mapped_column, relationship

from camp_planner.config import fk, table_name
from camp_planner.extensions import Base
from camp_planner.models.activity import ActivityTag  # runtime: used by the association_proxy creator
from camp_planner.models.common import TimestampMixin, portable_enum

if TYPE_CHECKING:
    from camp_planner.models.activity import Activity
    from camp_planner.models.google import GoogleSyncOp
    from camp_planner.models.material import Material
    from camp_planner.models.org import Org


class Camp(TimestampMixin, Base):
    """A single camp.

    Time model: each day is a 24h row anchored at window_start_min (minutes
    from midnight; default 240 = 04:00), so the row crosses midnight and a night
    program running until 04:00 stays on its own day's row. Activity datetimes
    are stored naive and interpreted in timezone.
    """

    __tablename__ = table_name("camps")

    # Columns:
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)

    start_date: Mapped[date]
    length_days: Mapped[int] = mapped_column(default=1)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Prague")

    # Minute-of-day each day-row starts at, and the editing-grid snap resolution.
    window_start_min: Mapped[int] = mapped_column(default=4 * 60)    # 04:00
    snap_minutes: Mapped[int] = mapped_column(default=15)

    # Optional camp location, used to shade the timeline by day/night (sun altitude).
    latitude: Mapped[float | None]
    longitude: Mapped[float | None]

    # Optimistic-lock token: bumped on any change to slot placement or the day
    # window, so a concurrent timeline edit can detect it raced.
    timeline_rev: Mapped[int] = mapped_column(default=0, server_default="0")

    # Google Calendar sync (optional; see services/google_sync.py + docs/GOOGLE_CALENDAR_SETUP.md).
    # Connected when google_calendar_id is set. google_sync_token is Google's incremental
    # events.list cursor for the inbound review; google_last_pull_at stamps the last pull.
    google_calendar_id: Mapped[str | None] = mapped_column(String(255))
    google_sync_token: Mapped[str | None] = mapped_column(Text)
    google_last_pull_at: Mapped[datetime | None]

    # Relationships:
    categories: Mapped[list[Category]] = relationship(
        back_populates="camp", cascade="all, delete-orphan", order_by="Category.sort_order"
    )
    orgs: Mapped[list[Org]] = relationship(
        back_populates="camp", cascade="all, delete-orphan", order_by="Org.initials"
    )
    activities: Mapped[list[Activity]] = relationship(
        back_populates="camp", cascade="all, delete-orphan", order_by="Activity.id"
    )
    materials: Mapped[list[Material]] = relationship(back_populates="camp", cascade="all, delete-orphan")
    tags: Mapped[list[Tag]] = relationship(
        back_populates="camp", cascade="all, delete-orphan", order_by="Tag.sort_order"
    )
    sync_ops: Mapped[list[GoogleSyncOp]] = relationship(
        back_populates="camp", cascade="all, delete-orphan"
    )

    @property
    def end_date(self) -> date:
        """Last calendar day of the camp (inclusive); the window spans length_days days."""
        return self.start_date + timedelta(days=self.length_days - 1)

    def __repr__(self) -> str:
        return f"<Camp {self.slug!r}>"


class Category(Base):
    """Per-camp activity category that drives the timeline color."""

    __tablename__ = table_name("categories")
    __table_args__ = (UniqueConstraint("camp_id", "key", name="uq_category_camp_key"),)

    # Columns:
    id: Mapped[int] = mapped_column(primary_key=True)
    camp_id: Mapped[int] = mapped_column(ForeignKey(fk("camps.id")))  # index already part of the uq_category_camp_key

    key: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(255))
    color: Mapped[str] = mapped_column(String(7), default="#9e9e9e")
    sort_order: Mapped[int] = mapped_column(default=0)

    # Relationships:
    camp: Mapped[Camp] = relationship(back_populates="categories")
    activities: Mapped[list[Activity]] = relationship(back_populates="category")


class TagKind(str, enum.Enum):
    """What value a tag carries per activity (the actual value lives on the
    activity↔tag join). label is a plain presence tag with no value."""

    label = "label"        # presence only — no per-activity value
    check = "check"        # done / not done
    progress = "progress"  # 0–100 %
    text = "text"          # free text


class Tag(Base):
    __tablename__ = table_name("tags")
    __table_args__ = (UniqueConstraint("camp_id", "name", name="uq_tag_camp_name"),)

    # Columns:
    id: Mapped[int] = mapped_column(primary_key=True)
    camp_id: Mapped[int] = mapped_column(ForeignKey(fk("camps.id")))  # index already part of the uq_tag_camp_name
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[TagKind] = mapped_column(portable_enum(TagKind, "tag_kind"), default=TagKind.label)
    pinned: Mapped[bool] = mapped_column(default=False)  # shown as a column on the activities status page
    sort_order: Mapped[int] = mapped_column(default=0)

    # Relationships:
    camp: Mapped[Camp] = relationship(back_populates="tags")
    # ActivityTag (the valued link) lives in activity.py, next to the activities.
    activity_links: Mapped[list[ActivityTag]] = relationship(
        back_populates="tag", cascade="all, delete-orphan"
    )
    # Plain Activity list — from a tag we just want which activities carry it;
    # the per-activity value matters from the activity side, not here.
    activities: AssociationProxy[list[Activity]] = association_proxy(
        "activity_links", "activity", creator=lambda activity: ActivityTag(activity=activity)
    )
