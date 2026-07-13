"""expand dish recognition model version

Revision ID: 20260713_0013
Revises: 20260710_0012
Create Date: 2026-07-13 14:30:00
"""

from alembic import op
import sqlalchemy as sa

from migrations.helpers import column_exists, table_exists


revision = "20260713_0013"
down_revision = "20260710_0012"
branch_labels = None
depends_on = None


def upgrade():
    if table_exists("dish_recognitions") and column_exists("dish_recognitions", "model_version"):
        op.alter_column(
            "dish_recognitions",
            "model_version",
            existing_type=sa.String(length=32),
            type_=sa.String(length=64),
            existing_nullable=True,
        )


def downgrade():
    if table_exists("dish_recognitions") and column_exists("dish_recognitions", "model_version"):
        op.execute(
            sa.text(
                "UPDATE dish_recognitions "
                "SET model_version = LEFT(model_version, 32) "
                "WHERE LENGTH(model_version) > 32"
            )
        )
        op.alter_column(
            "dish_recognitions",
            "model_version",
            existing_type=sa.String(length=64),
            type_=sa.String(length=32),
            existing_nullable=True,
        )
