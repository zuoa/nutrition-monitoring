"""Persist staged date matching and the calibration used by each result."""

from alembic import op
import sqlalchemy as sa

from migrations.helpers import add_column_if_not_exists, drop_column_if_exists, table_exists

revision = "20260914_0026"
down_revision = "20260831_0025"
branch_labels = None
depends_on = None


def upgrade():
    for name, type_ in (
        ("applied_time_offset_seconds", sa.Float()),
        ("match_round", sa.Integer()),
        ("match_window_seconds", sa.Integer()),
    ):
        add_column_if_not_exists("match_results", sa.Column(name, type_, nullable=True))
    if not table_exists("matching_runs"):
        op.create_table(
            "matching_runs",
            sa.Column("match_date", sa.Date(), primary_key=True),
            sa.Column("requested", sa.Integer(), nullable=False),
            sa.Column("completed", sa.Integer(), nullable=False),
            sa.Column("generation", sa.Integer(), nullable=False),
            sa.Column("phase", sa.String(20), nullable=False),
            sa.Column("stage", sa.Integer(), nullable=False),
            sa.Column("cursor", sa.Integer(), nullable=False),
            sa.Column("snapshot", sa.JSON(), nullable=True),
            sa.Column("assignments", sa.JSON(), nullable=False),
            sa.Column("notification_student_ids", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if not table_exists("matching_candidates"):
        op.create_table(
            "matching_candidates",
            sa.Column("match_date", sa.Date(), sa.ForeignKey("matching_runs.match_date", ondelete="CASCADE"), primary_key=True),
            sa.Column("record_id", sa.Integer(), primary_key=True),
            sa.Column("image_id", sa.Integer(), primary_key=True),
            sa.Column("time_diff_seconds", sa.Float(), nullable=False),
        )
        op.create_index("ix_matching_candidates_order", "matching_candidates", ["match_date", "time_diff_seconds", "record_id", "image_id"])


def downgrade():
    for table in ("matching_candidates", "matching_runs"):
        if table_exists(table):
            op.drop_table(table)
    for name in ("applied_time_offset_seconds", "match_round", "match_window_seconds"):
        drop_column_if_exists("match_results", name)
