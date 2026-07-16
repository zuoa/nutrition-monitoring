"""make video sync enqueue idempotent

Revision ID: 20260716_0021
Revises: 20260716_0020
Create Date: 2026-07-16 18:00:00
"""

from alembic import op
import sqlalchemy as sa

from migrations.helpers import drop_index_if_exists, table_exists


revision = "20260716_0021"
down_revision = "20260716_0020"
branch_labels = None
depends_on = None


INDEX_NAME = "uq_task_logs_active_video_sync_date"
ACTIVE_VIDEO_SYNC_WHERE = sa.text(
    "task_type IN ('video_source_sync', 'nvr_download') "
    "AND status IN ('pending', 'running')"
)


def upgrade():
    if not table_exists("task_logs"):
        return

    # Older releases could create more than one active row for a date because
    # the active-task check and Celery publish were separate operations. Keep
    # the newest row and release every older database lock before adding the
    # invariant that prevents the race from recurring.
    if table_exists("video_recording_jobs"):
        op.execute(
            sa.text(
                "WITH ranked AS ("
                " SELECT id, ROW_NUMBER() OVER (PARTITION BY task_date ORDER BY id DESC) AS row_num"
                " FROM task_logs"
                " WHERE task_date IS NOT NULL"
                " AND task_type IN ('video_source_sync', 'nvr_download')"
                " AND status IN ('pending', 'running')"
                ")"
                " UPDATE video_recording_jobs"
                " SET status = 'cancelled',"
                " stage = 'cancelled',"
                " error_code = 'duplicate_parent_task',"
                " error_message = '检测到重复的视频同步任务，升级时已自动释放',"
                " finished_at = CURRENT_TIMESTAMP,"
                " lease_expires_at = NULL,"
                " next_dispatch_at = NULL"
                " WHERE task_log_id IN (SELECT id FROM ranked WHERE row_num > 1)"
                " AND status NOT IN ('success', 'failed', 'cancelled')"
            )
        )
    op.execute(
        sa.text(
            "WITH ranked AS ("
            " SELECT id, ROW_NUMBER() OVER (PARTITION BY task_date ORDER BY id DESC) AS row_num"
            " FROM task_logs"
            " WHERE task_date IS NOT NULL"
            " AND task_type IN ('video_source_sync', 'nvr_download')"
            " AND status IN ('pending', 'running')"
            ")"
            " UPDATE task_logs"
            " SET status = 'failed',"
            " error_message = '检测到重复的视频同步任务，升级时已自动释放',"
            " finished_at = CURRENT_TIMESTAMP"
            " WHERE id IN (SELECT id FROM ranked WHERE row_num > 1)"
        )
    )

    inspector = sa.inspect(op.get_bind())
    if any(index["name"] == INDEX_NAME for index in inspector.get_indexes("task_logs")):
        return
    op.create_index(
        INDEX_NAME,
        "task_logs",
        ["task_date"],
        unique=True,
        postgresql_where=ACTIVE_VIDEO_SYNC_WHERE,
        sqlite_where=ACTIVE_VIDEO_SYNC_WHERE,
    )


def downgrade():
    drop_index_if_exists(INDEX_NAME, "task_logs")
