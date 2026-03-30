"""add task cancellation fields

Revision ID: 20260330_000002
Revises: 20260329_000001
Create Date: 2026-03-30 16:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260330_000002"
down_revision = "20260329_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("cancellation_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "tasks",
        sa.Column("cancellation_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
    )
    op.create_index("ix_tasks_cancellation_requested", "tasks", ["cancellation_requested"], unique=False)

    op.add_column(
        "execution_runs",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "execution_runs",
        sa.Column("cancel_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("execution_runs", "cancel_reason")
    op.drop_column("execution_runs", "cancelled_at")

    op.drop_index("ix_tasks_cancellation_requested", table_name="tasks")
    op.drop_column("tasks", "cancellation_reason")
    op.drop_column("tasks", "cancellation_requested_at")
    op.drop_column("tasks", "cancellation_requested")
