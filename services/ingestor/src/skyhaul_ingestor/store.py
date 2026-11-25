"""Postgres statements of the ingestor (SQLAlchemy Core, one call per batch)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final

from skyhaul_fleet_core.models import Drone, Telemetry
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

# Session.execute is typed as Result, which has no rowcount; for INSERT and DELETE it is a
# CursorResult, which does.

RETENTION_BATCH: Final = 10_000


def registered_ids(session: Session, drone_ids: set[str]) -> set[str]:
    rows = session.scalars(
        select(Drone.id).where(Drone.id.in_(drone_ids), Drone.deleted_at.is_(None))
    )
    return set(rows)


def insert_telemetry(session: Session, rows: Sequence[dict[str, Any]]) -> int:
    """Insert, skipping msg_ids already stored. Returns the number of new rows."""
    if not rows:
        return 0
    statement = (
        insert(Telemetry).values(list(rows)).on_conflict_do_nothing(index_elements=["msg_id"])
    )
    return session.execute(statement).rowcount  # type: ignore[attr-defined, no-any-return]


def delete_expired(session: Session, cutoff: datetime, limit: int = RETENTION_BATCH) -> int:
    """Delete up to ``limit`` rows with ``ts < cutoff``. Returns how many went."""
    oldest = select(Telemetry.msg_id).where(Telemetry.ts < cutoff).limit(limit)
    statement = delete(Telemetry).where(Telemetry.msg_id.in_(oldest.scalar_subquery()))
    return session.execute(statement).rowcount  # type: ignore[attr-defined, no-any-return]
