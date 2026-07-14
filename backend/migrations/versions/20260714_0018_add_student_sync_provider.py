"""add generic student sync provider identity

Revision ID: 20260714_0018
Revises: 20260714_0017
Create Date: 2026-07-14 18:00:00
"""

from alembic import op
import sqlalchemy as sa

from migrations.helpers import (
    add_column_if_not_exists,
    column_exists,
    create_index_if_not_exists,
    drop_column_if_exists,
    drop_index_if_exists,
    table_exists,
)


revision = "20260714_0018"
down_revision = "20260714_0017"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # PostgreSQL 枚举值无法在 downgrade 中无损删除；保留 api 值不影响旧代码。
        op.execute("ALTER TYPE studentsourceenum ADD VALUE IF NOT EXISTS 'api'")

    if not table_exists("students"):
        return
    add_column_if_not_exists(
        "students",
        sa.Column("sync_provider", sa.String(length=64), nullable=True),
    )
    add_column_if_not_exists(
        "students",
        sa.Column("external_id", sa.String(length=128), nullable=True),
    )
    add_column_if_not_exists(
        "students",
        sa.Column("registration_no", sa.String(length=64), nullable=True),
    )
    create_index_if_not_exists(
        op.f("ix_students_sync_provider"),
        "students",
        ["sync_provider"],
        unique=False,
    )
    create_index_if_not_exists(
        op.f("ix_students_registration_no"),
        "students",
        ["registration_no"],
        unique=False,
    )
    if column_exists("students", "dingtalk_user_id"):
        bind.execute(sa.text(
            "UPDATE students SET sync_provider = 'dingtalk', external_id = dingtalk_user_id "
            "WHERE dingtalk_user_id IS NOT NULL AND dingtalk_user_id <> '' "
            "AND sync_provider IS NULL "
            "AND dingtalk_user_id IN ("
            "SELECT dingtalk_user_id FROM students WHERE dingtalk_user_id IS NOT NULL "
            "GROUP BY dingtalk_user_id HAVING COUNT(*) = 1"
            ")"
        ))
    create_index_if_not_exists(
        "uq_students_sync_provider_external_id",
        "students",
        ["sync_provider", "external_id"],
        unique=True,
    )


def downgrade():
    drop_index_if_exists(
        "uq_students_sync_provider_external_id",
        table_name="students",
    )
    drop_index_if_exists(
        op.f("ix_students_sync_provider"),
        table_name="students",
    )
    drop_index_if_exists(
        op.f("ix_students_registration_no"),
        table_name="students",
    )
    drop_column_if_exists("students", "registration_no")
    drop_column_if_exists("students", "external_id")
    drop_column_if_exists("students", "sync_provider")
