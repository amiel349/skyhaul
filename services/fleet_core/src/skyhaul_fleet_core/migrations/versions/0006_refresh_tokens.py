"""Refresh tokens of the session cookie (ADR 0018), and the API role's grants on them.

skyhaul_api may DELETE here (unlike the other app tables): pruning removes expired rows.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GRANT = (
    "DO $$ BEGIN\n"
    "  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'skyhaul_api') THEN\n"
    "    GRANT SELECT, INSERT, UPDATE, DELETE ON refresh_tokens TO skyhaul_api;\n"
    "  END IF;\n"
    "END $$"
)


def upgrade() -> None:
    timestamp = sa.DateTime(timezone=True)
    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("csrf_hash", sa.LargeBinary(), nullable=False),
        sa.Column("issued_at", timestamp, nullable=False),
        sa.Column("expires_at", timestamp, nullable=False),
        sa.Column("used_at", timestamp, nullable=True),
        sa.Column("revoked_at", timestamp, nullable=True),
        sa.Column("replaced_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_refresh_tokens_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_refresh_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_expires_at", "refresh_tokens", ["expires_at"])
    op.execute(GRANT)


def downgrade() -> None:
    op.drop_table("refresh_tokens")
