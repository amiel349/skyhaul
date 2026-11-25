"""Index telemetry.ts so the retention delete does not scan the table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_telemetry_ts", "telemetry", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_telemetry_ts", table_name="telemetry")
