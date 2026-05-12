"""cache dish sample embeddings

Revision ID: 20260512_0005
Revises: 20260411_0004
Create Date: 2026-05-12 00:00:05
"""

from alembic import op
import sqlalchemy as sa


revision = "20260512_0005"
down_revision = "20260411_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("dish_sample_images", sa.Column("embedding_input_hash", sa.String(length=64), nullable=True))
    op.add_column("dish_sample_images", sa.Column("embedding_vector", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("dish_sample_images", "embedding_vector")
    op.drop_column("dish_sample_images", "embedding_input_hash")
