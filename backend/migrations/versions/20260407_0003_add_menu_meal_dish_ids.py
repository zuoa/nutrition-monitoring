"""add meal dish ids to daily menus

Revision ID: 20260407_0003
Revises: 20260403_0002
Create Date: 2026-04-07 00:00:03
"""

from alembic import op
import sqlalchemy as sa
from migrations.helpers import add_column_if_not_exists, column_exists, drop_column_if_exists


revision = "20260407_0003"
down_revision = "20260403_0002"
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_not_exists("daily_menus", sa.Column("meal_dish_ids", sa.JSON(), nullable=True))
    drop_column_if_exists("daily_menus", "dish_ids")


def downgrade():
    if not column_exists("daily_menus", "dish_ids"):
        op.add_column("daily_menus", sa.Column("dish_ids", sa.JSON(), nullable=True))
    drop_column_if_exists("daily_menus", "meal_dish_ids")
