"""add expanded dish nutrition fields

Revision ID: 20260708_0010
Revises: 20260707_0009
Create Date: 2026-07-08 00:10:00
"""
from alembic import op
import sqlalchemy as sa
from migrations.helpers import add_column_if_not_exists, drop_column_if_exists


revision = "20260708_0010"
down_revision = "20260707_0009"
branch_labels = None
depends_on = None


NEW_NUTRITION_COLUMNS = (
    "cholesterol",
    "added_sugar",
    "calcium",
    "iron",
    "zinc",
    "vitamin_a",
    "vitamin_c",
    "vitamin_d",
)


def upgrade():
    for column_name in NEW_NUTRITION_COLUMNS:
        add_column_if_not_exists("dishes", sa.Column(column_name, sa.Numeric(8, 2), nullable=True))


def downgrade():
    for column_name in reversed(NEW_NUTRITION_COLUMNS):
        drop_column_if_exists("dishes", column_name)

