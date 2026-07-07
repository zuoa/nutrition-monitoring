"""add captured image regions

Revision ID: 20260411_0004
Revises: 20260407_0003
Create Date: 2026-04-11 00:00:04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from migrations.helpers import create_index_if_not_exists, drop_index_if_exists, drop_table_if_exists, table_exists


revision = "20260411_0004"
down_revision = "20260407_0003"
branch_labels = None
depends_on = None


# create_type=False：类型由 upgrade() 里的 .create(checkfirst=True) 显式建一次，
# 避免 op.create_table 再通过 _on_table_create 重复 CREATE TYPE 报 DuplicateObject。
region_recognition_status_enum = postgresql.ENUM(
    "recognized",
    "low_confidence",
    "unrecognized",
    name="regionrecognitionstatusenum",
    create_type=False,
)

region_review_status_enum = postgresql.ENUM(
    "pending",
    "bound",
    "ignored",
    name="regionreviewstatusenum",
    create_type=False,
)


def upgrade():
    region_recognition_status_enum.create(op.get_bind(), checkfirst=True)
    region_review_status_enum.create(op.get_bind(), checkfirst=True)
    if not table_exists("captured_image_regions"):
        op.create_table(
            "captured_image_regions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("image_id", sa.Integer(), nullable=False),
            sa.Column("region_index", sa.Integer(), nullable=False),
            sa.Column("bbox", sa.JSON(), nullable=False),
            sa.Column("bbox_source", sa.String(length=32), nullable=False, server_default="pixels"),
            sa.Column("detector_source", sa.String(length=64), nullable=True),
            sa.Column("image_path", sa.String(length=512), nullable=False),
            sa.Column("recognition_status", region_recognition_status_enum, nullable=False, server_default="unrecognized"),
            sa.Column("suggested_dish_id", sa.Integer(), nullable=True),
            sa.Column("suggested_dish_name", sa.String(length=64), nullable=True),
            sa.Column("suggested_confidence", sa.Numeric(4, 3), nullable=True),
            sa.Column("review_status", region_review_status_enum, nullable=False, server_default="pending"),
            sa.Column("dish_sample_image_id", sa.Integer(), nullable=True),
            sa.Column("model_version", sa.String(length=64), nullable=True),
            sa.Column("raw_result", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["dish_sample_image_id"], ["dish_sample_images.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["image_id"], ["captured_images.id"]),
            sa.ForeignKeyConstraint(["suggested_dish_id"], ["dishes.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    create_index_if_not_exists(op.f("ix_captured_image_regions_image_id"), "captured_image_regions", ["image_id"], unique=False)
    create_index_if_not_exists(op.f("ix_captured_image_regions_recognition_status"), "captured_image_regions", ["recognition_status"], unique=False)
    create_index_if_not_exists(op.f("ix_captured_image_regions_review_status"), "captured_image_regions", ["review_status"], unique=False)
    create_index_if_not_exists(op.f("ix_captured_image_regions_suggested_dish_id"), "captured_image_regions", ["suggested_dish_id"], unique=False)


def downgrade():
    drop_index_if_exists(op.f("ix_captured_image_regions_suggested_dish_id"), table_name="captured_image_regions")
    drop_index_if_exists(op.f("ix_captured_image_regions_review_status"), table_name="captured_image_regions")
    drop_index_if_exists(op.f("ix_captured_image_regions_recognition_status"), table_name="captured_image_regions")
    drop_index_if_exists(op.f("ix_captured_image_regions_image_id"), table_name="captured_image_regions")
    drop_table_if_exists("captured_image_regions")
    region_review_status_enum.drop(op.get_bind(), checkfirst=True)
    region_recognition_status_enum.drop(op.get_bind(), checkfirst=True)
