"""add per-sample visual embedding status

Revision ID: 20260713_0014
Revises: 20260713_0013
Create Date: 2026-07-13 15:10:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from migrations.helpers import (
    add_column_if_not_exists,
    create_index_if_not_exists,
    drop_column_if_exists,
    drop_index_if_exists,
    table_exists,
)


revision = "20260713_0014"
down_revision = "20260713_0013"
branch_labels = None
depends_on = None


embedding_status_enum = postgresql.ENUM(
    "pending",
    "processing",
    "ready",
    "failed",
    name="embeddingstatusenum",
    create_type=False,
)


def upgrade():
    if not table_exists("dish_sample_images"):
        return

    embedding_status_enum.create(op.get_bind(), checkfirst=True)
    add_column_if_not_exists(
        "dish_sample_images",
        sa.Column(
            "visual_embedding_status",
            embedding_status_enum,
            nullable=False,
            server_default="pending",
        ),
    )
    add_column_if_not_exists(
        "dish_sample_images",
        sa.Column("visual_embedding_version", sa.String(length=64), nullable=True),
    )
    add_column_if_not_exists(
        "dish_sample_images",
        sa.Column("visual_embedding_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    add_column_if_not_exists(
        "dish_sample_images",
        sa.Column("visual_error_message", sa.String(length=255), nullable=True),
    )
    create_index_if_not_exists(
        op.f("ix_dish_sample_images_visual_embedding_status"),
        "dish_sample_images",
        ["visual_embedding_status"],
        unique=False,
    )


def downgrade():
    drop_index_if_exists(
        op.f("ix_dish_sample_images_visual_embedding_status"),
        table_name="dish_sample_images",
    )
    drop_column_if_exists("dish_sample_images", "visual_error_message")
    drop_column_if_exists("dish_sample_images", "visual_embedding_updated_at")
    drop_column_if_exists("dish_sample_images", "visual_embedding_version")
    drop_column_if_exists("dish_sample_images", "visual_embedding_status")
