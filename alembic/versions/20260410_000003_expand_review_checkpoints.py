"""expand review checkpoints

Revision ID: 20260410_000003
Revises: 20260330_000002
Create Date: 2026-04-10 16:25:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260410_000003"
down_revision = "20260330_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "review_checkpoints",
        sa.Column("reason_category", sa.String(length=64), nullable=False, server_default="other"),
    )
    op.add_column(
        "review_checkpoints",
        sa.Column("timeout_policy", sa.String(length=32), nullable=False, server_default="fail_closed"),
    )
    op.add_column(
        "review_checkpoints",
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_review_checkpoints_reason_category",
        "review_checkpoints",
        ["reason_category"],
        unique=False,
    )
    op.create_index(
        "ix_review_checkpoints_deadline_at",
        "review_checkpoints",
        ["deadline_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_review_checkpoints_deadline_at", table_name="review_checkpoints")
    op.drop_index("ix_review_checkpoints_reason_category", table_name="review_checkpoints")
    op.drop_column("review_checkpoints", "deadline_at")
    op.drop_column("review_checkpoints", "timeout_policy")
    op.drop_column("review_checkpoints", "reason_category")
