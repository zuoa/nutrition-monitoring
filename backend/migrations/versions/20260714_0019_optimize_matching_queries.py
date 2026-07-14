"""optimize matching queries

Revision ID: 20260714_0019
Revises: 20260714_0018
Create Date: 2026-07-14 20:00:00
"""

from alembic import op

from migrations.helpers import create_index_if_not_exists, drop_index_if_exists


revision = "20260714_0019"
down_revision = "20260714_0018"
branch_labels = None
depends_on = None


INDEXES = (
    (
        "ix_consumption_records_import_batch_time_id",
        "consumption_records",
        ["import_batch", "transaction_time", "id"],
    ),
    (
        "ix_captured_images_channel_captured_at",
        "captured_images",
        ["channel_id", "captured_at"],
    ),
    (
        "ix_match_results_date_status_image",
        "match_results",
        ["match_date", "status", "image_id"],
    ),
    (
        "ix_dish_recognitions_image_confidence_dish",
        "dish_recognitions",
        ["image_id", "is_low_confidence", "dish_id"],
    ),
)


def upgrade():
    for index_name, table_name, columns in INDEXES:
        create_index_if_not_exists(index_name, table_name, columns, unique=False)


def downgrade():
    for index_name, table_name, _columns in reversed(INDEXES):
        drop_index_if_exists(index_name, table_name=table_name)
