"""add student enrollment status

Revision ID: 20260713_0016
Revises: 20260713_0015
Create Date: 2026-07-13 18:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from migrations.helpers import (
    add_column_if_not_exists,
    create_index_if_not_exists,
    drop_column_if_exists,
    drop_index_if_exists,
    table_exists,
)


revision = "20260713_0016"
down_revision = "20260713_0015"
branch_labels = None
depends_on = None


enrollment_status_enum = postgresql.ENUM(
    "enrolled",
    "graduated",
    name="enrollmentstatusenum",
    create_type=False,
)


def upgrade():
    if not table_exists("students"):
        return
    enrollment_status_enum.create(op.get_bind(), checkfirst=True)
    add_column_if_not_exists(
        "students",
        sa.Column(
            "enrollment_status",
            enrollment_status_enum,
            nullable=False,
            server_default="enrolled",
        ),
    )
    create_index_if_not_exists(
        op.f("ix_students_enrollment_status"),
        "students",
        ["enrollment_status"],
        unique=False,
    )


def downgrade():
    drop_index_if_exists(op.f("ix_students_enrollment_status"), "students")
    drop_column_if_exists("students", "enrollment_status")
    enrollment_status_enum.drop(op.get_bind(), checkfirst=True)
