"""cache visual sample embeddings

Revision ID: 20260713_0015
Revises: 20260713_0014
Create Date: 2026-07-13 17:30:00
"""

from alembic import op
import sqlalchemy as sa

from migrations.helpers import add_column_if_not_exists, drop_column_if_exists, table_exists


revision = "20260713_0015"
down_revision = "20260713_0014"
branch_labels = None
depends_on = None


def upgrade():
    if not table_exists("dish_sample_images"):
        return
    add_column_if_not_exists(
        "dish_sample_images",
        sa.Column("visual_embedding_input_hash", sa.String(length=64), nullable=True),
    )


def downgrade():
    drop_column_if_exists("dish_sample_images", "visual_embedding_input_hash")
