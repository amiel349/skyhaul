"""Table grants of the service roles skyhaul_api and skyhaul_ingestor (ADR 0013).

The roles come from deploy/postgres/initdb.d/10-roles.sh. A database without them (a plain
test container) skips the grants, so the migrations run anywhere. A future table adds its
grants in its own migration.

- skyhaul_api: SELECT, INSERT and UPDATE on the app tables (soft deletes are UPDATEs, no
  DELETE), SELECT on telemetry, and only SELECT and INSERT on audit_log (append-only: it can
  never change or remove an entry; SELECT serves GET /audit).
- skyhaul_ingestor: what the ingestor writes, and SELECT where a WHERE, RETURNING or
  ON CONFLICT needs it. DELETE only on telemetry (retention).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_DML = "SELECT, INSERT, UPDATE"

GRANTS: dict[str, dict[str, str]] = {
    "skyhaul_api": {
        "users": APP_DML,
        "drones": APP_DML,
        "missions": APP_DML,
        "mission_events": APP_DML,
        "commands": APP_DML,
        "alerts": APP_DML,
        "telemetry": "SELECT",
        "audit_log": "SELECT, INSERT",
    },
    "skyhaul_ingestor": {
        "drones": "SELECT",
        "missions": "SELECT, UPDATE",
        "mission_events": "INSERT",
        "commands": "SELECT, UPDATE",
        "alerts": "SELECT, INSERT, UPDATE",
        "telemetry": "SELECT, INSERT, DELETE",
    },
}

SEQUENCES: dict[str, tuple[str, ...]] = {
    "skyhaul_api": ("mission_events_id_seq", "audit_log_id_seq"),
    "skyhaul_ingestor": ("mission_events_id_seq",),
}


def _if_role(role: str, statements: list[str]) -> str:
    body = "\n".join(f"    {statement};" for statement in statements)
    return (
        "DO $$ BEGIN\n"
        f"  IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN\n"
        f"{body}\n"
        "  END IF;\n"
        "END $$"
    )


def upgrade() -> None:
    for role, tables in GRANTS.items():
        statements = [f"GRANT {rights} ON {table} TO {role}" for table, rights in tables.items()]
        statements += [f"GRANT USAGE ON SEQUENCE {seq} TO {role}" for seq in SEQUENCES[role]]
        op.execute(_if_role(role, statements))


def downgrade() -> None:
    for role, tables in GRANTS.items():
        statements = [f"REVOKE ALL ON {table} FROM {role}" for table in tables]
        statements += [f"REVOKE ALL ON SEQUENCE {seq} FROM {role}" for seq in SEQUENCES[role]]
        op.execute(_if_role(role, statements))
