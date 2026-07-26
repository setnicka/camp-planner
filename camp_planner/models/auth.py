"""Standalone-mode users and their per-camp role grants.

These tables are created by the normal migrations in *every* deployment, but are
only populated when AUTH_MODE=standalone; under proxy/embedded modes they
stay empty (identity comes from the proxy headers or the host callback).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from camp_planner.auth.identity import CampRole
from camp_planner.config import fk, table_name
from camp_planner.extensions import Base
from camp_planner.models.common import TimestampMixin, portable_enum

if TYPE_CHECKING:
    from camp_planner.models.camp import Camp


class User(TimestampMixin, Base):
    __tablename__ = table_name("users")

    # Columns:
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(default=False)

    # Relationships:
    camp_roles: Mapped[list[UserCampRole]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    def __repr__(self) -> str:
        return f"<User {self.username!r}>"


class UserCampRole(Base):
    """A role grant: which role a user holds, over which camp.

    camp_id NULL means an *unscoped* grant — the role applies to all camps.
    """

    __tablename__ = table_name("user_camp_roles")
    __table_args__ = (
        UniqueConstraint("user_id", "camp_id", "role", name="uq_user_camp_role"),
    )

    # Columns:
    id: Mapped[int] = mapped_column(primary_key=True)
    # index already part of uq_user_camp_role (leading column)
    user_id: Mapped[int] = mapped_column(ForeignKey(fk("users.id"), ondelete="CASCADE"))
    # NULL = all camps. CASCADE: deleting a camp drops the scoped grants to it.
    camp_id: Mapped[int | None] = mapped_column(
        ForeignKey(fk("camps.id"), ondelete="CASCADE"), index=True
    )
    role: Mapped[CampRole] = mapped_column(portable_enum(CampRole, "camp_role"))

    # Relationships:
    user: Mapped[User] = relationship(back_populates="camp_roles")


class ApiToken(TimestampMixin, Base):
    """A bearer token for programmatic API access, scoped to one camp + role.

    Self-contained (not FK'd to User — that table is empty under proxy/embedded).
    Only the SHA-256 of the secret is stored; the secret is shown once at creation.
    Deleting the camp cascades its tokens away.
    """

    __tablename__ = table_name("api_tokens")
    # Unique per camp, not globally.
    __table_args__ = (UniqueConstraint("camp_id", "name", name="uq_api_token_camp_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    camp_id: Mapped[int] = mapped_column(ForeignKey(fk("camps.id"), ondelete="CASCADE"))
    role: Mapped[CampRole] = mapped_column(portable_enum(CampRole, "api_token_role"))
    created_by: Mapped[str] = mapped_column(String(255))
    last_used_at: Mapped[datetime | None] = mapped_column()

    camp: Mapped[Camp] = relationship()

    def __repr__(self) -> str:
        return f"<ApiToken {self.name!r} camp={self.camp_id} role={self.role.value}>"
