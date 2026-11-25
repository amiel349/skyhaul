"""Mission progress (waypoint_index, progress_pct) and the Idempotency-Key of POST /missions.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("missions", sa.Column("waypoint_index", sa.Integer(), nullable=True))
    op.add_column("missions", sa.Column("progress_pct", sa.Double(), nullable=True))
    op.add_column("missions", sa.Column("idempotency_key", sa.Text(), nullable=True))
    op.create_unique_constraint(
        "uq_missions_created_by_idempotency_key", "missions", ["created_by", "idempotency_key"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_missions_created_by_idempotency_key", "missions", type_="unique")
    op.drop_column("missions", "idempotency_key")
    op.drop_column("missions", "progress_pct")
    op.drop_column("missions", "waypoint_index")
