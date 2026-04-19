"""add execution run budget reports

Revision ID: 20260416_000004
Revises: 20260410_000003
Create Date: 2026-04-16 10:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260416_000004"
down_revision = "20260410_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "execution_runs",
        sa.Column(
            "budget_report",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("execution_runs", "budget_report")
