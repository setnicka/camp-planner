"""Import all models so SQLAlchemy's metadata is fully populated.

Anything that needs the model classes can do from camp_planner.models import Camp.
"""

from camp_planner.models.activity import (
    Activity,
    ActivityAssignment,
    ActivityTag,
    Todo,
    TodoAssignment,
)
from camp_planner.models.audit import AuditLog
from camp_planner.models.auth import User, UserCampRole
from camp_planner.models.camp import Camp, Category, Tag
from camp_planner.models.google import GoogleSyncOp, SyncOpKind
from camp_planner.models.material import (
    Material,
    MaterialAssignment,
    MaterialNeed,
)
from camp_planner.models.org import Org
from camp_planner.models.slot import Slot, SlotAssignment

__all__ = [
    "Activity",
    "ActivityAssignment",
    "ActivityTag",
    "AuditLog",
    "Camp",
    "Category",
    "GoogleSyncOp",
    "Material",
    "MaterialAssignment",
    "MaterialNeed",
    "Org",
    "Slot",
    "SlotAssignment",
    "SyncOpKind",
    "Tag",
    "Todo",
    "TodoAssignment",
    "User",
    "UserCampRole",
]
