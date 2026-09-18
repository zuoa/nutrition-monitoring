"""Persist externally pushed sport results and optional media files."""

from alembic import op
import sqlalchemy as sa

from migrations.helpers import table_exists

revision = "20260915_0027"
down_revision = "20260914_0026"
branch_labels = None
depends_on = None


def upgrade():
    if not table_exists("sport_records"):
        op.create_table(
            "sport_records",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("event_key", sa.String(64), nullable=False, unique=True),
            sa.Column("version", sa.String(16), nullable=False),
            sa.Column("school_id", sa.String(128), nullable=False),
            sa.Column("product_type", sa.Integer(), nullable=False),
            sa.Column("sport_type", sa.Integer(), nullable=False),
            sa.Column("mode", sa.Integer(), nullable=False),
            sa.Column("person_id", sa.String(64), nullable=False),
            sa.Column("start_time", sa.BigInteger(), nullable=False),
            sa.Column("score", sa.Float(), nullable=False),
            sa.Column("score_unit", sa.String(16)),
            sa.Column("all_time", sa.Float()),
            sa.Column("video_file_id", sa.String(256)),
            sa.Column("face_file_id", sa.String(256)),
            sa.Column("raw_result", sa.JSON(), nullable=False),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_sport_records_person_start", "sport_records", ["person_id", "start_time"])
        op.create_index("ix_sport_records_school_sport_start", "sport_records", ["school_id", "sport_type", "start_time"])
    if not table_exists("sport_files"):
        op.create_table(
            "sport_files",
            sa.Column("file_id", sa.String(256), primary_key=True),
            sa.Column("version", sa.String(16), nullable=False),
            sa.Column("file_type", sa.Integer(), nullable=False),
            sa.Column("storage_path", sa.Text(), nullable=False),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade():
    for table in ("sport_files", "sport_records"):
        if table_exists(table):
            op.drop_table(table)
