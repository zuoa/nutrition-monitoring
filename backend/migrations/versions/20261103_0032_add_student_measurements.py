"""Add student height (cm) and weight (kg), synchronized from BMI sport results."""

import sqlalchemy as sa

from migrations.helpers import add_column_if_not_exists, drop_column_if_exists

revision = "20261103_0032"
down_revision = "20261019_0031"
branch_labels = None
depends_on = None


def upgrade():
    # bootstrap-db may already have created these columns through create_all.
    add_column_if_not_exists("students", sa.Column("height", sa.Float(), nullable=True))
    add_column_if_not_exists("students", sa.Column("weight", sa.Float(), nullable=True))


def downgrade():
    drop_column_if_exists("students", "weight")
    drop_column_if_exists("students", "height")
