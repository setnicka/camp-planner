"""Add 'merge' to the audit_action enum

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-16 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# DB_TABLE_PREFIX is read at import time, so these build a prefixed or unprefixed schema
# to match the models either way.
from camp_planner.config import table_name


# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None

# audit_action is a non-native (CHECK-constrained) enum, so widening it means rewriting the
# CHECK — batch_alter_table recreates the table with the new allowed set on every backend.
_OLD = sa.Enum('create', 'update', 'delete', name='audit_action', native_enum=False)
_NEW = sa.Enum('create', 'update', 'delete', 'merge', name='audit_action', native_enum=False)


def upgrade():
    with op.batch_alter_table(table_name('audit_logs'), schema=None) as batch_op:
        batch_op.alter_column('action', existing_type=_OLD, type_=_NEW, existing_nullable=False)


def downgrade():
    with op.batch_alter_table(table_name('audit_logs'), schema=None) as batch_op:
        batch_op.alter_column('action', existing_type=_NEW, type_=_OLD, existing_nullable=False)
