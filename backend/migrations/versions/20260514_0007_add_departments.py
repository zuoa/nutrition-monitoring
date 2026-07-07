"""add departments

Revision ID: 20260514_0007
Revises: 20260513_0006
Create Date: 2026-05-14 00:00:07
"""

from alembic import op
import sqlalchemy as sa
from migrations.helpers import create_index_if_not_exists, drop_index_if_exists, drop_table_if_exists, table_exists


revision = "20260514_0007"
down_revision = "20260513_0006"
branch_labels = None
depends_on = None


def upgrade():
    if not table_exists("departments"):
        op.create_table(
            "departments",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("dingtalk_dept_id", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("parent_dingtalk_dept_id", sa.String(length=64), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("sync_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("dingtalk_dept_id"),
        )
    create_index_if_not_exists(op.f("ix_departments_dingtalk_dept_id"), "departments", ["dingtalk_dept_id"], unique=False)
    create_index_if_not_exists(op.f("ix_departments_parent_dingtalk_dept_id"), "departments", ["parent_dingtalk_dept_id"], unique=False)


def downgrade():
    drop_index_if_exists(op.f("ix_departments_parent_dingtalk_dept_id"), table_name="departments")
    drop_index_if_exists(op.f("ix_departments_dingtalk_dept_id"), table_name="departments")
    drop_table_if_exists("departments")
