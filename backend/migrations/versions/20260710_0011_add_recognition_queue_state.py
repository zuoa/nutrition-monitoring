"""add resilient recognition queue state

Revision ID: 20260710_0011
Revises: 20260708_0010
Create Date: 2026-07-10 16:00:00
"""
from alembic import op
import sqlalchemy as sa

from migrations.helpers import (
    add_column_if_not_exists,
    create_index_if_not_exists,
    drop_column_if_exists,
    drop_index_if_exists,
)


revision = "20260710_0011"
down_revision = "20260708_0010"
branch_labels = None
depends_on = None


NEW_IMAGE_STATUSES = ("queued", "processing", "retry_wait", "invalid")


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for value in NEW_IMAGE_STATUSES:
            bind.execute(sa.text(f"ALTER TYPE imagestatusenum ADD VALUE IF NOT EXISTS '{value}'"))

    add_column_if_not_exists(
        "captured_images",
        sa.Column("recognition_attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    add_column_if_not_exists("captured_images", sa.Column("recognition_task_id", sa.String(64), nullable=True))
    add_column_if_not_exists("captured_images", sa.Column("recognition_task_log_id", sa.Integer(), nullable=True))
    add_column_if_not_exists("captured_images", sa.Column("recognition_started_at", sa.DateTime(timezone=True), nullable=True))
    add_column_if_not_exists("captured_images", sa.Column("recognition_finished_at", sa.DateTime(timezone=True), nullable=True))
    add_column_if_not_exists("captured_images", sa.Column("recognition_lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    add_column_if_not_exists("captured_images", sa.Column("recognition_error_code", sa.String(64), nullable=True))
    add_column_if_not_exists("captured_images", sa.Column("recognition_error_message", sa.Text(), nullable=True))
    add_column_if_not_exists(
        "task_logs",
        sa.Column("invalid_count", sa.Integer(), nullable=False, server_default="0"),
    )

    create_index_if_not_exists("ix_captured_images_recognition_task_id", "captured_images", ["recognition_task_id"])
    create_index_if_not_exists("ix_captured_images_recognition_task_log_id", "captured_images", ["recognition_task_log_id"])
    create_index_if_not_exists(
        "ix_captured_images_recognition_lease_expires_at",
        "captured_images",
        ["recognition_lease_expires_at"],
    )


def downgrade():
    drop_index_if_exists("ix_captured_images_recognition_lease_expires_at", "captured_images")
    drop_index_if_exists("ix_captured_images_recognition_task_log_id", "captured_images")
    drop_index_if_exists("ix_captured_images_recognition_task_id", "captured_images")

    drop_column_if_exists("task_logs", "invalid_count")
    for column_name in (
        "recognition_error_message",
        "recognition_error_code",
        "recognition_lease_expires_at",
        "recognition_finished_at",
        "recognition_started_at",
        "recognition_task_log_id",
        "recognition_task_id",
        "recognition_attempt_count",
    ):
        drop_column_if_exists("captured_images", column_name)

    # PostgreSQL enum values are intentionally retained. Removing enum values safely
    # requires rebuilding every dependent column and is unnecessary for rollback.
