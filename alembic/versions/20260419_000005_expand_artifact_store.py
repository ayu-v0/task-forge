"""expand artifact store

Revision ID: 20260419_000005
Revises: 20260416_000004
Create Date: 2026-04-19 22:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260419_000005"
down_revision = "20260416_000004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "artifacts",
        sa.Column(
            "raw_content",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "artifacts",
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "artifacts",
        sa.Column(
            "structured_output",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "artifacts",
        sa.Column("schema_version", sa.String(length=32), nullable=False, server_default="artifact.v1"),
    )


def downgrade() -> None:
    op.drop_column("artifacts", "schema_version")
    op.drop_column("artifacts", "structured_output")
    op.drop_column("artifacts", "summary")
    op.drop_column("artifacts", "raw_content")
