"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}
# Generate with DB_TABLE_PREFIX unset (clean literals), then wrap every NEW name so the
# migration stays prefix-portable: table_name('t') for tables, _fk('t.id') for FK targets,
# _ix('ix_t_col') for auto index names (leave explicitly-named indexes as plain literals).
from camp_planner.config import table_name, table_name as _fk, index_name as _ix

# revision identifiers, used by Alembic.
revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade():
    ${upgrades if upgrades else "pass"}


def downgrade():
    ${downgrades if downgrades else "pass"}
