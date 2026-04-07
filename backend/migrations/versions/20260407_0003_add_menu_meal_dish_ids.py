"""add meal dish ids to daily menus

Revision ID: 20260407_0003
Revises: 20260403_0002
Create Date: 2026-04-07 00:00:03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260407_0003"
down_revision = "20260403_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("daily_menus", sa.Column("meal_dish_ids", sa.JSON(), nullable=True))
    op.drop_column("daily_menus", "dish_ids")


def downgrade():
    op.add_column("daily_menus", sa.Column("dish_ids", sa.JSON(), nullable=True))
    op.drop_column("daily_menus", "meal_dish_ids")
