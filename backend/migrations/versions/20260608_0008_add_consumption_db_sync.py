"""add consumption db sync metadata

Revision ID: 20260608_0008
Revises: 20260514_0007
Create Date: 2026-06-08 00:00:08
"""

from alembic import op
import sqlalchemy as sa


revision = "20260608_0008"
down_revision = "20260514_0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("consumption_records", sa.Column("source_system", sa.String(length=32), nullable=True))
    op.add_column("consumption_records", sa.Column("source_record_id", sa.String(length=128), nullable=True))
    op.add_column("consumption_records", sa.Column("source_payload", sa.JSON(), nullable=True))
    op.add_column("consumption_records", sa.Column("source_synced_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_consumption_records_source_system"), "consumption_records", ["source_system"], unique=False)
    op.create_index(op.f("ix_consumption_records_source_record_id"), "consumption_records", ["source_record_id"], unique=False)
    op.create_unique_constraint(
        "uq_consumption_source_record",
        "consumption_records",
        ["source_system", "source_record_id"],
    )

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
    op.create_index(op.f("ix_consumption_sync_states_cursor_transaction_time"), "consumption_sync_states", ["cursor_transaction_time"], unique=False)
    op.create_index(op.f("ix_consumption_sync_states_source_system"), "consumption_sync_states", ["source_system"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_consumption_sync_states_source_system"), table_name="consumption_sync_states")
    op.drop_index(op.f("ix_consumption_sync_states_cursor_transaction_time"), table_name="consumption_sync_states")
    op.drop_table("consumption_sync_states")

    op.drop_constraint("uq_consumption_source_record", "consumption_records", type_="unique")
    op.drop_index(op.f("ix_consumption_records_source_record_id"), table_name="consumption_records")
    op.drop_index(op.f("ix_consumption_records_source_system"), table_name="consumption_records")
    op.drop_column("consumption_records", "source_synced_at")
    op.drop_column("consumption_records", "source_payload")
    op.drop_column("consumption_records", "source_record_id")
    op.drop_column("consumption_records", "source_system")
