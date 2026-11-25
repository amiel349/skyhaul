"""Partial index for the sweeper's PENDING -> FAILED scan (commands stuck before the publish).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_commands_created_at_pending",
        "commands",
        ["created_at"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_index("ix_commands_created_at_pending", table_name="commands")
