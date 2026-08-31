"""add reusable dish meal-slot tags

Revision ID: 20260831_0025
Revises: 20260818_0024
Create Date: 2026-08-31 00:00:25
"""

from alembic import op
import sqlalchemy as sa

from migrations.helpers import table_exists


revision = "20260831_0025"
down_revision = "20260818_0024"
branch_labels = None
depends_on = None


def upgrade():
    if table_exists("dish_meal_slots"):
        return
    op.create_table(
        "dish_meal_slots",
        sa.Column("dish_id", sa.Integer(), nullable=False),
        sa.Column("meal_slot", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["dish_id"], ["dishes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("dish_id", "meal_slot"),
    )


def downgrade():
    if table_exists("dish_meal_slots"):
        op.drop_table("dish_meal_slots")
