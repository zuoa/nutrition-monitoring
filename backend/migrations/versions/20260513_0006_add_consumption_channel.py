"""add consumption channel

Revision ID: 20260513_0006
Revises: 20260512_0005
Create Date: 2026-05-13 00:00:06
"""

from alembic import op
import sqlalchemy as sa


revision = "20260513_0006"
down_revision = "20260512_0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("consumption_records", sa.Column("channel_id", sa.String(length=16), nullable=True))
    op.create_index(op.f("ix_consumption_records_channel_id"), "consumption_records", ["channel_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_consumption_records_channel_id"), table_name="consumption_records")
    op.drop_column("consumption_records", "channel_id")
