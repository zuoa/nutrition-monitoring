"""add time calibration samples

Revision ID: 20260723_0023
Revises: 20260716_0022
Create Date: 2026-07-23 00:00:23
"""

from alembic import op
import sqlalchemy as sa
from migrations.helpers import (
    create_index_if_not_exists,
    drop_index_if_exists,
    drop_table_if_exists,
    table_exists,
)


revision = "20260723_0023"
down_revision = "20260716_0022"
branch_labels = None
depends_on = None


def upgrade():
    if not table_exists("time_calibration_samples"):
        op.create_table(
            "time_calibration_samples",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("source_system", sa.String(length=32), nullable=False),
            sa.Column("source_time", sa.DateTime(timezone=False), nullable=False),
            sa.Column("local_time", sa.DateTime(timezone=False), nullable=False),
            sa.Column("offset_seconds", sa.Float(), nullable=False),
            sa.Column("rtt_ms", sa.Float(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    create_index_if_not_exists(
        op.f("ix_time_calibration_samples_source_system"),
        "time_calibration_samples",
        ["source_system"],
        unique=False,
    )
    create_index_if_not_exists(
        op.f("ix_time_calibration_samples_created_at"),
        "time_calibration_samples",
        ["created_at"],
        unique=False,
    )


def downgrade():
    drop_index_if_exists(
        op.f("ix_time_calibration_samples_created_at"),
        table_name="time_calibration_samples",
    )
    drop_index_if_exists(
        op.f("ix_time_calibration_samples_source_system"),
        table_name="time_calibration_samples",
    )
    drop_table_if_exists("time_calibration_samples")
