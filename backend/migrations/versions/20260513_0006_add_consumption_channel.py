"""add consumption channel

Revision ID: 20260513_0006
Revises: 20260512_0005
Create Date: 2026-05-13 00:00:06
"""

from alembic import op
import sqlalchemy as sa
from migrations.helpers import add_column_if_not_exists, create_index_if_not_exists, drop_column_if_exists, drop_index_if_exists


revision = "20260513_0006"
down_revision = "20260512_0005"
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_not_exists("consumption_records", sa.Column("channel_id", sa.String(length=16), nullable=True))
    create_index_if_not_exists(op.f("ix_consumption_records_channel_id"), "consumption_records", ["channel_id"], unique=False)


def downgrade():
    drop_index_if_exists(op.f("ix_consumption_records_channel_id"), table_name="consumption_records")
    drop_column_if_exists("consumption_records", "channel_id")
