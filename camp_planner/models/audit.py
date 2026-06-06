"""Append-only audit log of every change, filterable per activity."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from camp_planner.config import fk, table_name
from camp_planner.extensions import Base
from camp_planner.models.common import portable_enum


class AuditAction(str, enum.Enum):
    create = "create"
    update = "update"
    delete = "delete"


class EntityType(str, enum.Enum):
    """What an audit row is about."""
    camp = "camp"
    activity = "activity"
    timeline = "timeline"               # a batch slot placement (no single entity id)
    slot = "slot"                       # one slot: created/moved/resized/removed, or its attendees changed
    assignment = "assignment"           # an activity's org roles (garant/helper)
    tag = "tag"                         # an activity's tag link / value
    todo = "todo"
    material = "material"               # a catalog material
    material_need = "material_need"     # an activity's use of a material
    category = "category"
    org = "org"


class AuditLog(Base):
    """One recorded change.

    activity_id groups related changes (slot / assignment / material edits)
    under the activity they belong to, so an activity's full history is one
    query. changes is a structured field-level diff: {field: [before,
    after]}. The log is append-only; rows are never updated or deleted
    (enforced in the service layer, not the schema).

    author is the identity the host app reports for the logged-in user
    (username or its own id); auth/users live entirely in the host system.
    """

    __tablename__ = table_name("audit_logs")
    __table_args__ = (
        # per-entity history: WHERE entity_type = ? AND entity_id = ?
        Index("ix_audit_entity", "entity_type", "entity_id"),
    )

    # Columns:
    id: Mapped[int] = mapped_column(primary_key=True)
    # CASCADE: deleting a whole camp removes its audit trail too (nothing left to attach it to).
    camp_id: Mapped[int] = mapped_column(ForeignKey(fk("camps.id"), ondelete="CASCADE"), index=True)
    # SET NULL, not CASCADE: the audit trail outlives a deleted activity.
    activity_id: Mapped[int | None] = mapped_column(
        ForeignKey(fk("activities.id"), ondelete="SET NULL"), index=True
    )

    entity_type: Mapped[EntityType] = mapped_column(portable_enum(EntityType, "entity_type"))
    entity_id: Mapped[int | None]
    action: Mapped[AuditAction] = mapped_column(portable_enum(AuditAction, "audit_action"))

    author: Mapped[str | None] = mapped_column(String(255), index=True)  # host-provided identity
    message: Mapped[str | None] = mapped_column(String(500))   # optional commit message
    changes: Mapped[dict | None] = mapped_column(JSON)         # {field: [before, after]}

    created_at: Mapped[datetime] = mapped_column(default=func.now(), index=True)

    def __repr__(self) -> str:
        return f"<AuditLog {self.action.value} {self.entity_type.value}#{self.entity_id}>"
