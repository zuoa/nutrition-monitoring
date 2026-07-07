"""add video sources

Revision ID: 20260403_0002
Revises: 20260330_0001
Create Date: 2026-04-03 00:00:02
"""

from alembic import op
import sqlalchemy as sa
from migrations.helpers import create_index_if_not_exists, drop_index_if_exists, drop_table_if_exists, table_exists


revision = "20260403_0002"
down_revision = "20260330_0001"
branch_labels = None
depends_on = None


def upgrade():
    if not table_exists("video_sources"):
        op.create_table(
            "video_sources",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("source_type", sa.String(length=64), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="enabled"),
            sa.Column("config_json", sa.JSON(), nullable=False),
            sa.Column("credentials_json_encrypted", sa.Text(), nullable=False, server_default=""),
            sa.Column("last_validation_status", sa.String(length=32), nullable=False, server_default="unknown"),
            sa.Column("last_validation_error", sa.Text(), nullable=True),
            sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    create_index_if_not_exists(op.f("ix_video_sources_source_type"), "video_sources", ["source_type"], unique=False)
    create_index_if_not_exists(op.f("ix_video_sources_is_active"), "video_sources", ["is_active"], unique=False)
    create_index_if_not_exists(op.f("ix_video_sources_status"), "video_sources", ["status"], unique=False)


def downgrade():
    drop_index_if_exists(op.f("ix_video_sources_status"), table_name="video_sources")
    drop_index_if_exists(op.f("ix_video_sources_is_active"), table_name="video_sources")
    drop_index_if_exists(op.f("ix_video_sources_source_type"), table_name="video_sources")
    drop_table_if_exists("video_sources")
