"""api_tokens: camp-scoped API bearer tokens

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-26 16:21:20.612278

"""
from alembic import op
import sqlalchemy as sa

# DB_TABLE_PREFIX is read at import time, so these build a prefixed or unprefixed schema
# to match the models either way. table_name also prefixes FK targets
# ("camps.id" -> "<prefix>camps.id"); index_name prefixes auto index names.
from camp_planner.config import table_name, table_name as _fk, index_name as _ix

# revision identifiers, used by Alembic.
revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(table_name('api_tokens'),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('camp_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.Enum('editor', 'viewer', name='api_token_role', native_enum=False), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=False),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['camp_id'], [_fk('camps.id')], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('camp_id', 'name', name='uq_api_token_camp_name')
    )
    with op.batch_alter_table(table_name('api_tokens'), schema=None) as batch_op:
        # camp_id needs no standalone index: it's the leading column of uq_api_token_camp_name.
        batch_op.create_index(batch_op.f(_ix('ix_api_tokens_name')), ['name'], unique=False)
        batch_op.create_index(batch_op.f(_ix('ix_api_tokens_token_hash')), ['token_hash'], unique=True)


def downgrade():
    with op.batch_alter_table(table_name('api_tokens'), schema=None) as batch_op:
        batch_op.drop_index(batch_op.f(_ix('ix_api_tokens_token_hash')))
        batch_op.drop_index(batch_op.f(_ix('ix_api_tokens_name')))

    op.drop_table(table_name('api_tokens'))
