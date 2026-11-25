"""Postgres statements of the ingestor (SQLAlchemy Core, one call per batch)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final

from skyhaul_fleet_core.models import Command, CommandStatus, Drone, Telemetry
from skyhaul_fleet_core.transitions import COMMAND_TRANSITIONS, allowed_from
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skyhaul_ingestor.handlers import Reply

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


def apply_reply(session: Session, reply: Reply) -> Command | None:
    """Move the command to ACKED or NACKED if this drone sent it and its status allows the
    move (the forward-only table). A reply after TIMED_OUT sets ``late``. Returns the updated
    command, or None for a duplicate, unknown or mismatched reply (nothing changes)."""
    statement = (
        update(Command)
        .where(
            Command.id == reply.command_id,
            Command.drone_id == reply.drone_id,
            Command.status.in_(allowed_from(COMMAND_TRANSITIONS, reply.status)),
        )
        # SET expressions read the row before the update: late is the old status.
        .values(
            status=reply.status,
            replied_at=reply.replied_at,
            nack_reason=reply.nack_reason,
            nack_detail=reply.nack_detail,
            late=Command.status == CommandStatus.TIMED_OUT,
        )
        .returning(Command)
    )
    return session.scalars(statement).first()


def sweep_commands(
    session: Session, *, sent_before: datetime, created_before: datetime
) -> list[Command]:
    """SENT without a reply since ``sent_before`` becomes TIMED_OUT; PENDING since
    ``created_before`` (never confirmed by the broker) becomes FAILED."""
    timed_out = (
        update(Command)
        .where(Command.status == CommandStatus.SENT, Command.sent_at < sent_before)
        .values(status=CommandStatus.TIMED_OUT)
        .returning(Command)
    )
    failed = (
        update(Command)
        .where(Command.status == CommandStatus.PENDING, Command.created_at < created_before)
        .values(status=CommandStatus.FAILED)
        .returning(Command)
    )
    return [*session.scalars(timed_out), *session.scalars(failed)]
