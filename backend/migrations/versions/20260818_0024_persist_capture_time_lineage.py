"""persist capture time lineage on downstream analysis records

Revision ID: 20260818_0024
Revises: 20260723_0023
Create Date: 2026-08-18 00:00:24
"""

from alembic import op
import sqlalchemy as sa

from migrations.helpers import (
    add_column_if_not_exists,
    create_index_if_not_exists,
    drop_column_if_exists,
    drop_index_if_exists,
    table_exists,
)


revision = "20260818_0024"
down_revision = "20260723_0023"
branch_labels = None
depends_on = None


TABLES = (
    "dish_recognitions",
    "captured_image_regions",
    "match_results",
)


def upgrade():
    for table_name in TABLES:
        if not table_exists(table_name):
            continue
        add_column_if_not_exists(
            table_name,
            sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.execute(sa.text(
            f"""
            UPDATE {table_name}
            SET captured_at = (
                SELECT captured_images.captured_at
                FROM captured_images
                WHERE captured_images.id = {table_name}.image_id
            )
            WHERE captured_at IS NULL
              AND image_id IS NOT NULL
            """
        ))
        create_index_if_not_exists(
            op.f(f"ix_{table_name}_captured_at"),
            table_name,
            ["captured_at"],
            unique=False,
        )


def downgrade():
    for table_name in reversed(TABLES):
        drop_index_if_exists(
            op.f(f"ix_{table_name}_captured_at"),
            table_name=table_name,
        )
        drop_column_if_exists(table_name, "captured_at")
