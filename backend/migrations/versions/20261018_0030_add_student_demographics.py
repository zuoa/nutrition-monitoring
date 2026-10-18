"""Add optional student age and birth date for nutrition references."""

from alembic import op
import sqlalchemy as sa

revision = "20261018_0030"
down_revision = "20261018_0029"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("students", sa.Column("birth_date", sa.Date(), nullable=True))
    op.add_column("students", sa.Column("age", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("students", "age")
    op.drop_column("students", "birth_date")
