"""add durable per-recording video jobs

Revision ID: 20260710_0012
Revises: 20260710_0011
Create Date: 2026-07-10 19:45:00
"""
from alembic import op
import sqlalchemy as sa

from migrations.helpers import (
    add_column_if_not_exists,
    create_foreign_key_if_not_exists,
    create_index_if_not_exists,
    drop_column_if_exists,
    drop_index_if_exists,
    drop_table_if_exists,
    table_exists,
)


revision = "20260710_0012"
down_revision = "20260710_0011"
branch_labels = None
depends_on = None


def upgrade():
    if not table_exists("video_recording_jobs"):
        op.create_table(
            "video_recording_jobs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("task_log_id", sa.Integer(), nullable=False),
            sa.Column("video_source_id", sa.Integer(), nullable=True),
            sa.Column("channel_id", sa.String(32), nullable=False),
            sa.Column("filename", sa.String(512), nullable=False),
            sa.Column("video_path", sa.String(1024), nullable=False),
            sa.Column("output_dir", sa.String(1024), nullable=False),
            sa.Column("download_url", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("stage", sa.String(32), nullable=False, server_default="queued"),
            sa.Column("recording_start", sa.DateTime(timezone=True)),
            sa.Column("recording_end", sa.DateTime(timezone=True)),
            sa.Column("source_start", sa.DateTime(timezone=True)),
            sa.Column("source_end", sa.DateTime(timezone=True)),
            sa.Column("progress_percent", sa.Float(), nullable=False, server_default="0"),
            sa.Column("current_frame", sa.Integer()),
            sa.Column("total_frames", sa.Integer()),
            sa.Column("extracted_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("frame_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("download_attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("extract_attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("download_task_id", sa.String(64)),
            sa.Column("extract_task_id", sa.String(64)),
            sa.Column("extraction_strategy", sa.String(64)),
            sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("error_code", sa.String(64)),
            sa.Column("error_message", sa.Text()),
            sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("download_started_at", sa.DateTime(timezone=True)),
            sa.Column("download_finished_at", sa.DateTime(timezone=True)),
            sa.Column("extract_started_at", sa.DateTime(timezone=True)),
            sa.Column("extract_finished_at", sa.DateTime(timezone=True)),
            sa.Column("last_progress_at", sa.DateTime(timezone=True)),
            sa.Column("finished_at", sa.DateTime(timezone=True)),
            sa.ForeignKeyConstraint(["task_log_id"], ["task_logs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["video_source_id"], ["video_sources.id"], ondelete="SET NULL"),
            sa.UniqueConstraint("task_log_id", "filename", name="uq_video_recording_job_task_filename"),
        )

    for name, columns in (
        ("ix_video_recording_jobs_task_log_id", ["task_log_id"]),
        ("ix_video_recording_jobs_video_source_id", ["video_source_id"]),
        ("ix_video_recording_jobs_channel_id", ["channel_id"]),
        ("ix_video_recording_jobs_status", ["status"]),
        ("ix_video_recording_jobs_stage", ["stage"]),
        ("ix_video_recording_jobs_download_task_id", ["download_task_id"]),
        ("ix_video_recording_jobs_extract_task_id", ["extract_task_id"]),
        ("ix_video_recording_jobs_last_progress_at", ["last_progress_at"]),
    ):
        create_index_if_not_exists(name, "video_recording_jobs", columns)

    add_column_if_not_exists("captured_images", sa.Column("video_recording_job_id", sa.Integer(), nullable=True))
    create_foreign_key_if_not_exists(
        "fk_captured_images_video_recording_job_id",
        "captured_images",
        "video_recording_jobs",
        ["video_recording_job_id"],
        ["id"],
    )
    create_index_if_not_exists(
        "ix_captured_images_video_recording_job_id",
        "captured_images",
        ["video_recording_job_id"],
    )


def downgrade():
    drop_index_if_exists("ix_captured_images_video_recording_job_id", "captured_images")
    drop_column_if_exists("captured_images", "video_recording_job_id")
    drop_table_if_exists("video_recording_jobs")
