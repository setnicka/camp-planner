"""Slot display-name override

Revision ID: a1b2c3d4e5f6
Revises: db22112590d1
Create Date: 2026-06-14 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# DB_TABLE_PREFIX is read at import time, so these build a prefixed or unprefixed schema
# to match the models either way. table_name also prefixes FK targets
# ("camps.id" -> "<prefix>camps.id"); index_name prefixes auto index names.
from camp_planner.config import table_name


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'db22112590d1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table(table_name('slots'), schema=None) as batch_op:
        batch_op.add_column(sa.Column('override_name', sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table(table_name('slots'), schema=None) as batch_op:
        batch_op.drop_column('override_name')
