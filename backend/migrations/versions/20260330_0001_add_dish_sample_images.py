"""add dish sample images

Revision ID: 20260330_0001
Revises:
Create Date: 2026-03-30 00:00:01
"""

from alembic import op
import sqlalchemy as sa


revision = "20260330_0001"
down_revision = None
branch_labels = None
depends_on = None


embedding_status_enum = sa.Enum(
    "pending",
    "processing",
    "ready",
    "failed",
    name="embeddingstatusenum",
)


def upgrade():
    embedding_status_enum.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "dish_sample_images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dish_id", sa.Integer(), nullable=False),
        sa.Column("image_path", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_cover", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("embedding_status", embedding_status_enum, nullable=False, server_default="pending"),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.Column("embedding_version", sa.String(length=64), nullable=True),
        sa.Column("embedding_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["dish_id"], ["dishes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_dish_sample_images_dish_id"), "dish_sample_images", ["dish_id"], unique=False)
    op.create_index(
        op.f("ix_dish_sample_images_embedding_status"),
        "dish_sample_images",
        ["embedding_status"],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f("ix_dish_sample_images_embedding_status"), table_name="dish_sample_images")
    op.drop_index(op.f("ix_dish_sample_images_dish_id"), table_name="dish_sample_images")
    op.drop_table("dish_sample_images")
    embedding_status_enum.drop(op.get_bind(), checkfirst=True)
