"""inventory: the global warehouse (boxes, items, photos, inventory checks)

Also relaxes audit_logs.camp_id to NULL, for a change that belongs to no camp. On
SQLite that rebuilds audit_logs, copying the whole log.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-18 13:32:54.713404

"""
from alembic import op
import sqlalchemy as sa

# DB_TABLE_PREFIX is read at import time, so these build a prefixed or unprefixed schema
# to match the models either way. table_name also prefixes FK targets
# ("camps.id" -> "<prefix>camps.id"); index_name prefixes auto index names.
from camp_planner.config import table_name, table_name as _fk, index_name as _ix

# revision identifiers, used by Alembic.
revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(table_name('inventory_boxes'),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('location', sa.String(length=255), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('virtual', sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name', name='uq_inventory_box_name')
    )
    op.create_table(table_name('inventory_checks'),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('active_lock', sa.Integer(), nullable=True),
    sa.Column('author', sa.String(length=255), nullable=False),
    sa.Column('summary', sa.JSON(), nullable=True),
    sa.Column('camp_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['camp_id'], [_fk('camps.id')], name='fk_inventory_check_camp', ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    # 1 = active, NULL = finished. Repeated NULLs are allowed on every backend, so this
    # is a portable "at most one active check".
    sa.UniqueConstraint('active_lock', name='uq_inventory_check_active')
    )
    op.create_table(table_name('inventory_items'),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('alt_names', sa.JSON(), nullable=False),
    sa.Column('url', sa.String(length=1024), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('unit', sa.String(length=40), nullable=True),
    sa.Column('count', sa.Float(), nullable=True),
    sa.Column('box_id', sa.Integer(), nullable=True),
    sa.Column('discarded_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    # No ondelete: a box that still holds items must not be deletable.
    sa.ForeignKeyConstraint(['box_id'], [_fk('inventory_boxes.id')], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table(table_name('inventory_items'), schema=None) as batch_op:
        batch_op.create_index(batch_op.f(_ix('ix_inventory_items_box_id')), ['box_id'], unique=False)

    op.create_table(table_name('inventory_check_records'),
    sa.Column('check_id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('discarded', sa.Boolean(), nullable=False),
    sa.Column('count', sa.Float(), nullable=True),
    sa.Column('unit', sa.String(length=40), nullable=True),
    sa.Column('box_id', sa.Integer(), nullable=True),
    # from_box_id is a passive snapshot with no FK: SET NULL would erase it on box delete.
    sa.Column('from_box_id', sa.Integer(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('author', sa.String(length=255), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    # SET NULL: deleting a box keeps the observations, they just lose their place.
    sa.ForeignKeyConstraint(['box_id'], [_fk('inventory_boxes.id')], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['check_id'], [_fk('inventory_checks.id')], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['item_id'], [_fk('inventory_items.id')], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('check_id', 'item_id')
    )
    with op.batch_alter_table(table_name('inventory_check_records'), schema=None) as batch_op:
        batch_op.create_index(batch_op.f(_ix('ix_inventory_check_records_box_id')), ['box_id'], unique=False)
        batch_op.create_index(batch_op.f(_ix('ix_inventory_check_records_item_id')), ['item_id'], unique=False)

    op.create_table(table_name('inventory_photos'),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('filename', sa.String(length=64), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['item_id'], [_fk('inventory_items.id')], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table(table_name('inventory_photos'), schema=None) as batch_op:
        batch_op.create_index(batch_op.f(_ix('ix_inventory_photos_item_id')), ['item_id'], unique=False)

    with op.batch_alter_table(table_name('materials'), schema=None) as batch_op:
        batch_op.add_column(sa.Column('inventory_item_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_material_inventory_item', table_name('inventory_items'),
                                    ['inventory_item_id'], ['id'], ondelete='SET NULL')
        batch_op.create_unique_constraint('uq_material_camp_item', ['inventory_item_id', 'camp_id'])

    with op.batch_alter_table(table_name('audit_logs'), schema=None) as batch_op:
        batch_op.alter_column('camp_id',
               existing_type=sa.INTEGER(),
               nullable=True)


def downgrade():
    # Warehouse audit rows have no camp, so they cannot survive camp_id going NOT NULL.
    op.execute(
        sa.text(f"DELETE FROM {table_name('audit_logs')} WHERE camp_id IS NULL")
    )
    with op.batch_alter_table(table_name('audit_logs'), schema=None) as batch_op:
        batch_op.alter_column('camp_id',
               existing_type=sa.INTEGER(),
               nullable=False)

    with op.batch_alter_table(table_name('materials'), schema=None) as batch_op:
        # The foreign key first: MySQL refuses to drop the index it uses.
        batch_op.drop_constraint('fk_material_inventory_item', type_='foreignkey')
        batch_op.drop_constraint('uq_material_camp_item', type_='unique')
        batch_op.drop_column('inventory_item_id')

    with op.batch_alter_table(table_name('inventory_photos'), schema=None) as batch_op:
        batch_op.drop_index(batch_op.f(_ix('ix_inventory_photos_item_id')))

    op.drop_table(table_name('inventory_photos'))
    with op.batch_alter_table(table_name('inventory_check_records'), schema=None) as batch_op:
        batch_op.drop_index(batch_op.f(_ix('ix_inventory_check_records_item_id')))
        batch_op.drop_index(batch_op.f(_ix('ix_inventory_check_records_box_id')))

    op.drop_table(table_name('inventory_check_records'))
    with op.batch_alter_table(table_name('inventory_items'), schema=None) as batch_op:
        batch_op.drop_index(batch_op.f(_ix('ix_inventory_items_box_id')))

    op.drop_table(table_name('inventory_items'))
    op.drop_table(table_name('inventory_checks'))
    op.drop_table(table_name('inventory_boxes'))
