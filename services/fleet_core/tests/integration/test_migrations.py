"""The fleet_core migrations on a real Postgres (#24)."""

from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text

pytestmark = pytest.mark.integration

TABLES = {
    "users",
    "drones",
    "missions",
    "mission_events",
    "commands",
    "alerts",
    "telemetry",
    "audit_log",
}


def _schema(engine: Engine) -> dict[str, object]:
    """Tables, columns, indexes and enum types: enough to see that a migration changed nothing."""
    inspector = inspect(engine)
    with engine.connect() as connection:
        indexes = connection.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'")
        ).all()
        enums = connection.execute(
            text("SELECT typname FROM pg_type WHERE typtype = 'e' ORDER BY typname")
        ).all()
    return {
        "columns": {
            table: [(c["name"], str(c["type"])) for c in inspector.get_columns(table)]
            for table in inspector.get_table_names()
        },
        "indexes": sorted(indexes),
        "enums": enums,
    }


def test_migrations_create_every_m2_table(engine: Engine) -> None:
    assert set(inspect(engine).get_table_names()) >= TABLES


def test_downgrade_upgrade_and_repeat_upgrade_succeed_without_drift(
    engine: Engine, alembic_cfg: Config
) -> None:
    at_head = _schema(engine)

    command.downgrade(alembic_cfg, "base")
    assert not TABLES & set(inspect(engine).get_table_names())
    command.upgrade(alembic_cfg, "head")
    assert _schema(engine) == at_head

    command.upgrade(alembic_cfg, "head")  # already at head: a no-op
    assert _schema(engine) == at_head

    command.check(alembic_cfg)  # raises if the models and the migrations differ
