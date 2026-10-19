"""Add optional confirmed food and cooking labels to dishes."""

from alembic import op
import sqlalchemy as sa

revision = "20261019_0031"
down_revision = "20261018_0030"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("dishes", sa.Column("food_groups", sa.JSON(), nullable=True))
    op.add_column("dishes", sa.Column("cooking_method", sa.String(16), nullable=True))
    op.add_column("dishes", sa.Column("vegetable_types", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("dishes", "vegetable_types")
    op.drop_column("dishes", "cooking_method")
    op.drop_column("dishes", "food_groups")
