"""add resident meal slots for daily menu defaults

Revision ID: 20261012_0028
Revises: 20260915_0027
Create Date: 2026-10-12 00:00:28
"""

from alembic import op
import sqlalchemy as sa

from migrations.helpers import table_exists


revision = "20261012_0028"
down_revision = "20260915_0027"
branch_labels = None
depends_on = None


def upgrade():
    if table_exists("dish_resident_meal_slots"):
        return
    op.create_table(
        "dish_resident_meal_slots",
        sa.Column("dish_id", sa.Integer(), nullable=False),
        sa.Column("meal_slot", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["dish_id"], ["dishes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("dish_id", "meal_slot"),
    )


def downgrade():
    if table_exists("dish_resident_meal_slots"):
        op.drop_table("dish_resident_meal_slots")
