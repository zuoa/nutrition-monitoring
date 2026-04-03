"""add video sources

Revision ID: 20260403_0002
Revises: 20260330_0001
Create Date: 2026-04-03 00:00:02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260403_0002"
down_revision = "20260330_0001"
branch_labels = None
depends_on = None


def upgrade():
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
    op.create_index(op.f("ix_video_sources_source_type"), "video_sources", ["source_type"], unique=False)
    op.create_index(op.f("ix_video_sources_is_active"), "video_sources", ["is_active"], unique=False)
    op.create_index(op.f("ix_video_sources_status"), "video_sources", ["status"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_video_sources_status"), table_name="video_sources")
    op.drop_index(op.f("ix_video_sources_is_active"), table_name="video_sources")
    op.drop_index(op.f("ix_video_sources_source_type"), table_name="video_sources")
    op.drop_table("video_sources")
