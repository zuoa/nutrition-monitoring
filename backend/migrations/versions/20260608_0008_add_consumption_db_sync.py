"""add consumption db sync metadata

Revision ID: 20260608_0008
Revises: 20260514_0007
Create Date: 2026-06-08 00:00:08
"""

from alembic import op
import sqlalchemy as sa
from migrations.helpers import (
    add_column_if_not_exists,
    create_index_if_not_exists,
    create_unique_constraint_if_not_exists,
    drop_column_if_exists,
    drop_constraint_if_exists,
    drop_index_if_exists,
    drop_table_if_exists,
    table_exists,
)


revision = "20260608_0008"
down_revision = "20260514_0007"
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_not_exists("consumption_records", sa.Column("source_system", sa.String(length=32), nullable=True))
    add_column_if_not_exists("consumption_records", sa.Column("source_record_id", sa.String(length=128), nullable=True))
    add_column_if_not_exists("consumption_records", sa.Column("source_payload", sa.JSON(), nullable=True))
    add_column_if_not_exists("consumption_records", sa.Column("source_synced_at", sa.DateTime(timezone=True), nullable=True))
    create_index_if_not_exists(op.f("ix_consumption_records_source_system"), "consumption_records", ["source_system"], unique=False)
    create_index_if_not_exists(op.f("ix_consumption_records_source_record_id"), "consumption_records", ["source_record_id"], unique=False)
    create_unique_constraint_if_not_exists(
        "uq_consumption_source_record",
        "consumption_records",
        ["source_system", "source_record_id"],
    )

    if not table_exists("consumption_sync_states"):
        op.create_table(
            "consumption_sync_states",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("source_system", sa.String(length=32), nullable=False),
            sa.Column("cursor_transaction_time", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cursor_source_record_id", sa.String(length=128), nullable=True),
            sa.Column("last_batch_id", sa.String(length=64), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_success_count", sa.Integer(), nullable=True),
            sa.Column("last_skipped_count", sa.Integer(), nullable=True),
            sa.Column("last_error_count", sa.Integer(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("source_system"),
        )
    create_index_if_not_exists(op.f("ix_consumption_sync_states_cursor_transaction_time"), "consumption_sync_states", ["cursor_transaction_time"], unique=False)
    create_index_if_not_exists(op.f("ix_consumption_sync_states_source_system"), "consumption_sync_states", ["source_system"], unique=False)


def downgrade():
    drop_index_if_exists(op.f("ix_consumption_sync_states_source_system"), table_name="consumption_sync_states")
    drop_index_if_exists(op.f("ix_consumption_sync_states_cursor_transaction_time"), table_name="consumption_sync_states")
    drop_table_if_exists("consumption_sync_states")

    drop_constraint_if_exists("uq_consumption_source_record", "consumption_records", type_="unique")
    drop_index_if_exists(op.f("ix_consumption_records_source_record_id"), table_name="consumption_records")
    drop_index_if_exists(op.f("ix_consumption_records_source_system"), table_name="consumption_records")
    drop_column_if_exists("consumption_records", "source_synced_at")
    drop_column_if_exists("consumption_records", "source_payload")
    drop_column_if_exists("consumption_records", "source_record_id")
    drop_column_if_exists("consumption_records", "source_system")
