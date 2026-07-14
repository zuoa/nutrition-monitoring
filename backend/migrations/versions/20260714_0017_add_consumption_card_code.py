"""add consumption card code

Revision ID: 20260714_0017
Revises: 20260713_0016
Create Date: 2026-07-14 10:00:00
"""

from alembic import op
import sqlalchemy as sa

from migrations.helpers import (
    add_column_if_not_exists,
    create_index_if_not_exists,
    drop_column_if_exists,
    drop_index_if_exists,
)


revision = "20260714_0017"
down_revision = "20260713_0016"
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_not_exists(
        "consumption_records",
        sa.Column("card_code", sa.String(length=64), nullable=True),
    )
    create_index_if_not_exists(
        op.f("ix_consumption_records_card_code"),
        "consumption_records",
        ["card_code"],
        unique=False,
    )


def downgrade():
    drop_index_if_exists(
        op.f("ix_consumption_records_card_code"),
        table_name="consumption_records",
    )
    drop_column_if_exists("consumption_records", "card_code")
