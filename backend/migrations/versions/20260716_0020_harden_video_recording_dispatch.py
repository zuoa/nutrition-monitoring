"""harden durable video recording dispatch

Revision ID: 20260716_0020
Revises: 20260714_0019
Create Date: 2026-07-16 16:00:00
"""

from alembic import op
import sqlalchemy as sa

from migrations.helpers import (
    add_column_if_not_exists,
    create_index_if_not_exists,
    drop_column_if_exists,
    drop_index_if_exists,
    table_exists,
)


revision = "20260716_0020"
down_revision = "20260714_0019"
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_not_exists(
        "video_recording_jobs",
        sa.Column("dispatch_attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    add_column_if_not_exists(
        "video_recording_jobs",
        sa.Column("recovery_count", sa.Integer(), nullable=False, server_default="0"),
    )
    add_column_if_not_exists(
        "video_recording_jobs",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    add_column_if_not_exists(
        "video_recording_jobs",
        sa.Column("next_dispatch_at", sa.DateTime(timezone=True), nullable=True),
    )
    add_column_if_not_exists(
        "video_recording_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    create_index_if_not_exists(
        "ix_video_recording_jobs_next_dispatch_at",
        "video_recording_jobs",
        ["next_dispatch_at"],
    )
    create_index_if_not_exists(
        "ix_video_recording_jobs_lease_expires_at",
        "video_recording_jobs",
        ["lease_expires_at"],
    )

    # Existing queued rows predate the embedded outbox fields. Leaving
    # published_at NULL intentionally makes the reconciler republish the same
    # execution token; the atomic worker claim makes duplicate delivery safe.
    if table_exists("video_recording_jobs"):
        op.execute(
            "UPDATE video_recording_jobs "
            "SET next_dispatch_at = COALESCE(last_progress_at, queued_at) "
            "WHERE stage IN ('queued_download', 'queued_extract') "
            "AND next_dispatch_at IS NULL"
        )


def downgrade():
    drop_index_if_exists("ix_video_recording_jobs_lease_expires_at", "video_recording_jobs")
    drop_index_if_exists("ix_video_recording_jobs_next_dispatch_at", "video_recording_jobs")
    for column_name in (
        "lease_expires_at",
        "next_dispatch_at",
        "published_at",
        "recovery_count",
        "dispatch_attempt_count",
    ):
        drop_column_if_exists("video_recording_jobs", column_name)
