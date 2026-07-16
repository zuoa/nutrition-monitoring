"""add weekly grade and campus report types

Revision ID: 20260716_0022
Revises: 20260716_0021
Create Date: 2026-07-16 20:00:00
"""

from alembic import op


revision = "20260716_0022"
down_revision = "20260716_0021"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE reporttypeenum ADD VALUE IF NOT EXISTS 'grade_weekly'")
        op.execute("ALTER TYPE reporttypeenum ADD VALUE IF NOT EXISTS 'campus_weekly'")


def downgrade():
    # PostgreSQL enum values cannot be removed safely without rebuilding the type.
    pass
