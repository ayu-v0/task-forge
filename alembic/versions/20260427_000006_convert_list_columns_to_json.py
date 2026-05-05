"""convert list columns to json

Revision ID: 20260427_000006
Revises: 20260419_000005
Create Date: 2026-04-27 11:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260427_000006"
down_revision = "20260419_000005"
branch_labels = None
depends_on = None


def _is_postgresql_array(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return False

    inspector = sa.inspect(bind)
    for column in inspector.get_columns(table_name):
        if column["name"] == column_name:
            return isinstance(column["type"], postgresql.ARRAY)
    return False


def _convert_array_column_to_json(table_name: str, column_name: str) -> None:
    if not _is_postgresql_array(table_name, column_name):
        return

    op.execute(
        sa.text(f'ALTER TABLE "{table_name}" ALTER COLUMN "{column_name}" DROP DEFAULT')
    )
    op.alter_column(
        table_name,
        column_name,
        type_=sa.JSON(),
        postgresql_using=f"to_json({column_name})",
        existing_nullable=False,
    )


def upgrade() -> None:
    _convert_array_column_to_json("agent_roles", "capabilities")
    _convert_array_column_to_json("tasks", "dependency_ids")
    _convert_array_column_to_json("execution_runs", "logs")


def downgrade() -> None:
    pass
