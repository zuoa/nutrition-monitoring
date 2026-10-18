"""Add monthly class and campus report types.

Revision ID: 20261018_0029
Revises: 20261012_0028
"""

from alembic import op

revision = "20261018_0029"
down_revision = "20261012_0028"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE reporttypeenum ADD VALUE IF NOT EXISTS 'class_monthly'")
        op.execute("ALTER TYPE reporttypeenum ADD VALUE IF NOT EXISTS 'campus_monthly'")


def downgrade():
    # PostgreSQL enum values cannot be removed without rebuilding the type.
    pass
