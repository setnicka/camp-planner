"""Drop camps.google_sync_token; acquisition_labels NOT NULL

camps.google_sync_token was never read (the incremental-sync path it fed was
unreachable); materials.acquisition_labels always holds a list in practice — NULL
rows (predating the column's default) are backfilled to [] and the column tightened.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-26 12:00:00.000000

"""
from alembic import context, op
import sqlalchemy as sa

# DB_TABLE_PREFIX is read at import time, so these build a prefixed or unprefixed schema
# to match the models either way. table_name also prefixes FK targets
# ("todos.id" -> "<prefix>todos.id"); index_name prefixes auto index names.
from camp_planner.config import table_name, table_name as _fk, index_name as _ix  # noqa: F401


# revision identifiers, used by Alembic.
revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table(table_name('camps'), schema=None) as batch_op:
        batch_op.drop_column('google_sync_token')

    # Offline SQL (--sql) can't render a JSON literal — skip the backfill there;
    # NULL rows must then be backfilled by hand before the NOT NULL alter below.
    if not context.is_offline_mode():
        materials = sa.table(table_name('materials'), sa.column('acquisition_labels', sa.JSON()))
        op.execute(materials.update()
                   .where(materials.c.acquisition_labels.is_(None))
                   .values(acquisition_labels=[]))
    with op.batch_alter_table(table_name('materials'), schema=None) as batch_op:
        batch_op.alter_column('acquisition_labels', existing_type=sa.JSON(), nullable=False)


def downgrade():
    with op.batch_alter_table(table_name('materials'), schema=None) as batch_op:
        batch_op.alter_column('acquisition_labels', existing_type=sa.JSON(), nullable=True)

    with op.batch_alter_table(table_name('camps'), schema=None) as batch_op:
        batch_op.add_column(sa.Column('google_sync_token', sa.Text(), nullable=True))
