"""Postgres statements of the ingestor (SQLAlchemy Core, one call per batch)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final

from skyhaul_fleet_core.missions import failure_reason
from skyhaul_fleet_core.models import (
    Alert,
    AlertCode,
    AlertSeverity,
    AlertSource,
    Command,
    CommandStatus,
    Drone,
    Mission,
    MissionEvent,
    MissionState,
    Telemetry,
)
from skyhaul_fleet_core.transitions import COMMAND_TRANSITIONS, MISSION_TRANSITIONS, allowed_from
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skyhaul_ingestor.handlers import MissionUpdate, Reply

# Session.execute is typed as Result, which has no rowcount; for INSERT and DELETE it is a
# CursorResult, which does.

RETENTION_BATCH: Final = 10_000
DISPATCH_TIMEOUT: Final = "DISPATCH_TIMEOUT: no ACTIVE status in time"
LINK_LOST_MESSAGE: Final = "No heartbeat from the drone."


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
            late=Command.status.in_((CommandStatus.TIMED_OUT, CommandStatus.FAILED)),
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


# -- missions ----------------------------------------------------------------------------


def move_mission(
    session: Session, mission_id: uuid.UUID, drone_id: str, target: MissionState, **values: Any
) -> Mission | None:
    """The forward-only CAS of the mission table, for this drone's mission only, plus its
    ``state_changed`` event. Returns the moved mission, or None when its state does not allow
    the move (terminal, out of order)."""
    statement = (
        update(Mission)
        .where(
            Mission.id == mission_id,
            Mission.drone_id == drone_id,
            Mission.state.in_(allowed_from(MISSION_TRANSITIONS, target)),
        )
        .values(state=target, **values)
        .returning(Mission)
    )
    mission = session.scalars(statement).first()
    if mission is not None:
        reason = values.get("failure_reason")
        data = {"state": target.value, **({"reason": reason} if reason else {})}
        add_mission_event(session, mission.id, "state_changed", data)
    return mission


def add_mission_event(
    session: Session, mission_id: uuid.UUID, kind: str, data: dict[str, Any]
) -> None:
    # The list form: a single-row insert would add RETURNING id, which needs SELECT, and the
    # ingestor role has INSERT only on mission_events.
    row = {"mission_id": mission_id, "ts": func.now(), "kind": kind, "data": data}
    session.execute(insert(MissionEvent).values([row]))


def mission_of_drone(session: Session, mission_id: uuid.UUID, drone_id: str) -> bool:
    found = session.scalar(
        select(Mission.id).where(Mission.id == mission_id, Mission.drone_id == drone_id)
    )
    return found is not None


def apply_mission_status(session: Session, status: MissionUpdate) -> Mission | None:
    """ACTIVE moves a DISPATCHING mission to ACTIVE and records the progress of an ACTIVE one
    (never backwards). COMPLETED or ABORTED ends a live mission. Returns the changed mission,
    or None when nothing changed."""
    if status.mission_id is None:
        return None
    progress = {"waypoint_index": status.waypoint_index, "progress_pct": status.progress_pct}
    if status.state is not MissionState.ACTIVE:
        return move_mission(
            session,
            status.mission_id,
            status.drone_id,
            status.state,
            ended_at=func.now(),
            **progress,
        )
    moved = move_mission(session, status.mission_id, status.drone_id, MissionState.ACTIVE)
    statement = (
        update(Mission)
        .where(
            Mission.id == status.mission_id,
            Mission.drone_id == status.drone_id,
            Mission.state == MissionState.ACTIVE,
            or_(Mission.progress_pct.is_(None), Mission.progress_pct <= status.progress_pct),
        )
        .values(**progress)
        .returning(Mission)
    )
    return session.scalars(statement).first() or moved


def apply_start_reply(session: Session, command: Command) -> Mission | None:
    """The START_MISSION reply is authoritative for its mission: an ACK makes it ACTIVE, a
    NACK makes it FAILED with the drone's reason (plain text data)."""
    if command.type != "START_MISSION" or command.mission_id is None:
        return None
    if command.status is CommandStatus.ACKED:
        return move_mission(session, command.mission_id, command.drone_id, MissionState.ACTIVE)
    reason = failure_reason(f"{command.nack_reason}: {command.nack_detail}")
    return move_mission(
        session,
        command.mission_id,
        command.drone_id,
        MissionState.FAILED,
        failure_reason=reason,
        ended_at=func.now(),
    )


def sweep_missions(session: Session, *, started_before: datetime) -> list[Mission]:
    """DISPATCHING since before ``started_before`` without an ACTIVE: FAILED."""
    statement = (
        update(Mission)
        .where(Mission.state == MissionState.DISPATCHING, Mission.started_at < started_before)
        .values(state=MissionState.FAILED, failure_reason=DISPATCH_TIMEOUT, ended_at=func.now())
        .returning(Mission)
    )
    failed = list(session.scalars(statement))
    for mission in failed:
        data = {"state": MissionState.FAILED.value, "reason": DISPATCH_TIMEOUT}
        add_mission_event(session, mission.id, "state_changed", data)
    return failed


# -- alerts ------------------------------------------------------------------------------


def insert_alerts(session: Session, rows: Sequence[dict[str, Any]]) -> list[Alert]:
    """Insert drone alerts, once per msg_id. Returns the new ones."""
    if not rows:
        return []
    statement = (
        insert(Alert)
        .values(list(rows))
        .on_conflict_do_nothing(index_elements=["msg_id"])
        .returning(Alert)
    )
    return list(session.scalars(statement))


def raise_link_lost(session: Session, drone_id: str, raised_at: datetime) -> Alert | None:
    """The derived LINK_LOST. The partial unique index keeps one open per drone, so a second
    watchdog or a restart inserts nothing (None)."""
    statement = (
        insert(Alert)
        .values(
            drone_id=drone_id,
            code=AlertCode.LINK_LOST,
            severity=AlertSeverity.WARNING,
            message=LINK_LOST_MESSAGE,
            source=AlertSource.DERIVED,
            raised_at=raised_at,
        )
        .on_conflict_do_nothing(
            index_elements=["drone_id", "code"],
            index_where=and_(Alert.source == AlertSource.DERIVED, Alert.cleared_at.is_(None)),
        )
        .returning(Alert)
    )
    return session.scalars(statement).first()


def clear_link_lost(session: Session, drone_id: str, cleared_at: datetime) -> list[Alert]:
    statement = (
        update(Alert)
        .where(
            Alert.drone_id == drone_id,
            Alert.code == AlertCode.LINK_LOST,
            Alert.source == AlertSource.DERIVED,
            Alert.cleared_at.is_(None),
        )
        .values(cleared_at=cleared_at)
        .returning(Alert)
    )
    return list(session.scalars(statement))


def open_link_lost(session: Session) -> set[str]:
    """Drones with an open LINK_LOST, so a restarted watchdog can still clear them."""
    rows = session.scalars(
        select(Alert.drone_id).where(
            Alert.code == AlertCode.LINK_LOST,
            Alert.source == AlertSource.DERIVED,
            Alert.cleared_at.is_(None),
        )
    )
    return set(rows)
