"""Todo org assignments

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-26 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# DB_TABLE_PREFIX is read at import time, so these build a prefixed or unprefixed schema
# to match the models either way. table_name also prefixes FK targets
# ("todos.id" -> "<prefix>todos.id"); index_name prefixes auto index names.
from camp_planner.config import table_name, table_name as _fk, index_name as _ix


# revision identifiers, used by Alembic.
revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(table_name('todo_assignments'),
    sa.Column('todo_id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['todo_id'], [_fk('todos.id')], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], [_fk('orgs.id')], ),
    sa.PrimaryKeyConstraint('todo_id', 'org_id')
    )
    with op.batch_alter_table(table_name('todo_assignments'), schema=None) as batch_op:
        batch_op.create_index(batch_op.f(_ix('ix_todo_assignments_org_id')), ['org_id'], unique=False)


def downgrade():
    with op.batch_alter_table(table_name('todo_assignments'), schema=None) as batch_op:
        batch_op.drop_index(batch_op.f(_ix('ix_todo_assignments_org_id')))
    op.drop_table(table_name('todo_assignments'))
