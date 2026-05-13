"""add users

Revision ID: 20260513_000007
Revises: 20260427_000006
Create Date: 2026-05-13 22:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260513_000007"
down_revision = "20260427_000006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=False)
    op.create_index("ix_users_enabled", "users", ["enabled"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_users_enabled", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
