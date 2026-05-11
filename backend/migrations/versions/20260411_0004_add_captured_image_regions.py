"""add captured image regions

Revision ID: 20260411_0004
Revises: 20260407_0003
Create Date: 2026-04-11 00:00:04
"""

from alembic import op
import sqlalchemy as sa


revision = "20260411_0004"
down_revision = "20260407_0003"
branch_labels = None
depends_on = None


region_recognition_status_enum = sa.Enum(
    "recognized",
    "low_confidence",
    "unrecognized",
    name="regionrecognitionstatusenum",
)

region_review_status_enum = sa.Enum(
    "pending",
    "bound",
    "ignored",
    name="regionreviewstatusenum",
)


def upgrade():
    region_recognition_status_enum.create(op.get_bind(), checkfirst=True)
    region_review_status_enum.create(op.get_bind(), checkfirst=True)
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
    op.create_index(op.f("ix_captured_image_regions_image_id"), "captured_image_regions", ["image_id"], unique=False)
    op.create_index(op.f("ix_captured_image_regions_recognition_status"), "captured_image_regions", ["recognition_status"], unique=False)
    op.create_index(op.f("ix_captured_image_regions_review_status"), "captured_image_regions", ["review_status"], unique=False)
    op.create_index(op.f("ix_captured_image_regions_suggested_dish_id"), "captured_image_regions", ["suggested_dish_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_captured_image_regions_suggested_dish_id"), table_name="captured_image_regions")
    op.drop_index(op.f("ix_captured_image_regions_review_status"), table_name="captured_image_regions")
    op.drop_index(op.f("ix_captured_image_regions_recognition_status"), table_name="captured_image_regions")
    op.drop_index(op.f("ix_captured_image_regions_image_id"), table_name="captured_image_regions")
    op.drop_table("captured_image_regions")
    region_review_status_enum.drop(op.get_bind(), checkfirst=True)
    region_recognition_status_enum.drop(op.get_bind(), checkfirst=True)
