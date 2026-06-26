"""Activities and their per-activity data: org assignments, todos, and the
activity↔tag join.

An **activity** is the base unit of the planner. Its placement on the timeline
is held by its **slots** (any number, each main/prep/cleanup, see slot.py).
Tags and categories are camp-level in camp.py; materials in material.py.
"""

from __future__ import annotations

import enum
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from camp_planner.config import fk, table_name
from camp_planner.extensions import Base
from camp_planner.models.common import TimestampMixin, portable_enum

if TYPE_CHECKING:
    from camp_planner.models.camp import Camp, Category, Tag
    from camp_planner.models.material import MaterialNeed
    from camp_planner.models.org import Org
    from camp_planner.models.slot import Slot


class ActivityType(str, enum.Enum):
    """What kind of activity, which drives how its description is sourced and
    how it's edited. Add members here (plus a handler reading Activity.config)
    to introduce new special types — no schema change needed.
    """

    basic = "basic"                      # description authored in-app (description_md)
    external = "external"                # description linked externally; config={"url": ...}
    external_lecture = "external_lecture"  # pulled from external scheduler; config={"external_slot_id": ...}


class OrgRole(str, enum.Enum):
    """An org's planning-level role on an activity (see ActivityAssignment)."""

    garant = "garant"   # lead(s) responsible for the activity
    helper = "helper"   # helps prepare the activity


class Activity(TimestampMixin, Base):
    __tablename__ = table_name("activities")

    # Columns:
    id: Mapped[int] = mapped_column(primary_key=True)
    camp_id: Mapped[int] = mapped_column(ForeignKey(fk("camps.id")), index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey(fk("categories.id")), index=True)

    type: Mapped[ActivityType] = mapped_column(
        portable_enum(ActivityType, "activity_type"), default=ActivityType.basic
    )
    title: Mapped[str] = mapped_column(String(255))
    description_md: Mapped[str | None] = mapped_column(Text)   # in-app Markdown body (basic type)

    # Type-specific settings, keyed by type (e.g. external → {"url": ...},
    # external_lecture → {"external_slot_id": ...}). New types add keys here and
    # a handler, with no schema change.
    config: Mapped[dict | None] = mapped_column(JSON)

    # Relationships:
    camp: Mapped[Camp] = relationship(back_populates="activities")
    category: Mapped[Category | None] = relationship(back_populates="activities")

    slots: Mapped[list[Slot]] = relationship(
        back_populates="activity", cascade="all, delete-orphan"
    )
    assignments: Mapped[list[ActivityAssignment]] = relationship(
        back_populates="activity", cascade="all, delete-orphan"
    )
    material_needs: Mapped[list[MaterialNeed]] = relationship(
        back_populates="activity", cascade="all, delete-orphan"
    )
    todos: Mapped[list[Todo]] = relationship(
        back_populates="activity", cascade="all, delete-orphan", order_by="Todo.id"
    )
    tags: Mapped[list[ActivityTag]] = relationship(
        back_populates="activity", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Activity {self.id} {self.title!r}>"


class ActivityTag(Base):
    __tablename__ = table_name("activity_tags")

    # Columns:
    activity_id: Mapped[int] = mapped_column(
        ForeignKey(fk("activities.id"), ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey(fk("tags.id"), ondelete="CASCADE"), primary_key=True, index=True
    )
    # Per-activity value, interpreted per Tag.kind: a check's done-state, a
    # progress 0–100, or free text. NULL = the tag applies but has no value yet;
    # an absent link means the tag is not applicable to the activity.
    value: Mapped[str | None] = mapped_column(Text)

    # Relationships:
    activity: Mapped[Activity] = relationship(back_populates="tags")
    tag: Mapped[Tag] = relationship(back_populates="activity_links")


class ActivityAssignment(Base):
    """Mapping of which orgs are garants/helpers of an activity.

    Distinct from who actually attends each time block (see SlotAssignment),
    a slot may be staffed by orgs who aren't the activity's garants/helpers.
    """

    __tablename__ = table_name("activity_assignments")

    # Columns: the natural key (activity, org, role) is the primary key
    activity_id: Mapped[int] = mapped_column(ForeignKey(fk("activities.id")), primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey(fk("orgs.id")), primary_key=True, index=True)
    role: Mapped[OrgRole] = mapped_column(portable_enum(OrgRole, "org_role"), primary_key=True)

    # Relationships:
    activity: Mapped[Activity] = relationship(back_populates="assignments")
    org: Mapped[Org] = relationship(back_populates="activity_assignments")


class Todo(Base):
    __tablename__ = table_name("todos")

    # Columns:
    id: Mapped[int] = mapped_column(primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey(fk("activities.id")), index=True)
    title: Mapped[str] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(index=True)
    is_done: Mapped[bool] = mapped_column(default=False)

    # Relationships:
    activity: Mapped[Activity] = relationship(back_populates="todos")
    assignments: Mapped[list[TodoAssignment]] = relationship(
        back_populates="todo", cascade="all, delete-orphan"
    )


class TodoAssignment(Base):
    """Which orgs are responsible for a todo (any number, no role distinction)."""

    __tablename__ = table_name("todo_assignments")

    # Columns: the natural key (todo, org) is the primary key
    todo_id: Mapped[int] = mapped_column(
        ForeignKey(fk("todos.id"), ondelete="CASCADE"), primary_key=True
    )
    org_id: Mapped[int] = mapped_column(ForeignKey(fk("orgs.id")), primary_key=True, index=True)

    # Relationships:
    todo: Mapped[Todo] = relationship(back_populates="assignments")
    org: Mapped[Org] = relationship(back_populates="todo_assignments")
