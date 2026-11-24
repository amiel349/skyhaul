"""Drone id rules, foreign keys and the CAS update, enforced by the real database (#24)."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

import pytest
from skyhaul_fleet_core.models import (
    Command,
    CommandStatus,
    Drone,
    Mission,
    MissionState,
    Role,
    Telemetry,
    User,
)
from skyhaul_fleet_core.transitions import cas_update
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _user(session: Session) -> User:
    user = User(
        email=f"{uuid.uuid4().hex}@example.test",
        password_hash=secrets.token_hex(8),
        role=Role.ADMIN,
    )
    session.add(user)
    session.flush()
    return user


def _drone(drone_id: str) -> Drone:
    return Drone(id=drone_id, name=drone_id, home_lat=32.0, home_lon=34.8)


def _mission(drone_id: str, user: User) -> Mission:
    return Mission(
        drone_id=drone_id,
        state=MissionState.PLANNED,
        waypoints=[],
        correlation_id=uuid.uuid4(),
        created_by=user.id,
    )


def _command(drone_id: str, user: User) -> Command:
    return Command(
        id=uuid.uuid4(),
        drone_id=drone_id,
        type="ARM",
        payload={},
        correlation_id=uuid.uuid4(),
        status=CommandStatus.PENDING,
        requested_by=user.id,
    )


def _rejected(session: Session, row: Drone | Mission | Command) -> bool:
    """True if the database refuses to insert ``row``. A Core insert bypasses the ORM identity
    map, so the database itself decides; the savepoint keeps the session usable."""
    table = type(row).__table__
    values = {c.key: getattr(row, c.key) for c in table.columns if getattr(row, c.key) is not None}
    if isinstance(row, Mission):
        values["id"] = uuid.uuid4()
    try:
        with session.begin_nested():
            session.execute(insert(table).values(values))
    except IntegrityError:
        return True
    return False


def test_drone_ids_are_unique_forever_valid_and_referenced_by_foreign_keys(
    session: Session,
) -> None:
    user = _user(session)
    drone = _drone("d-001")
    session.add(drone)
    session.flush()

    assert _rejected(session, _drone("d-001"))  # taken

    drone.deleted_at = datetime.now(UTC)
    session.flush()
    assert _rejected(session, _drone("d-001"))  # deleted ids are never reused

    for bad_id in ("D-001", "-d001", "d_001", "d" * 65, ""):
        assert _rejected(session, _drone(bad_id)), bad_id  # breaks the DroneId pattern

    assert _rejected(session, _mission("ghost", user))
    assert _rejected(session, _command("ghost", user))
    assert not _rejected(session, _mission("d-001", user))


def test_cas_update_moves_a_row_only_from_an_allowed_state(session: Session) -> None:
    user = _user(session)
    session.add(_drone("d-cas"))
    session.flush()  # no relationship() tells the ORM to insert the drone first
    mission = _mission("d-cas", user)
    command = _command("d-cas", user)
    session.add_all([mission, command])
    session.flush()

    def mission_state() -> MissionState:
        session.expire(mission)
        return mission.state

    assert not cas_update(session, Mission, mission.id, MissionState.ACTIVE)  # PLANNED -> ACTIVE
    assert mission_state() is MissionState.PLANNED
    assert cas_update(session, Mission, mission.id, MissionState.DISPATCHING)
    assert cas_update(session, Mission, mission.id, MissionState.COMPLETED)
    assert not cas_update(session, Mission, mission.id, MissionState.ACTIVE)  # terminal
    assert mission_state() is MissionState.COMPLETED

    sent_at = datetime.now(UTC)
    assert cas_update(session, Command, command.id, CommandStatus.SENT, sent_at=sent_at)
    assert cas_update(session, Command, command.id, CommandStatus.TIMED_OUT)
    assert cas_update(session, Command, command.id, CommandStatus.ACKED, late=True)
    assert not cas_update(session, Command, command.id, CommandStatus.NACKED)  # terminal
    session.expire(command)
    assert (command.status, command.late, command.sent_at) == (CommandStatus.ACKED, True, sent_at)


def test_drone_initiated_telemetry_without_a_correlation_id_is_stored(session: Session) -> None:
    session.add(
        Telemetry(
            msg_id=uuid.uuid4(),
            drone_id="main-d001",
            type="BATTERY",
            ts=datetime.now(UTC),
            correlation_id=None,
            payload={"charge_pct": 80.0, "est_remaining_s": 900},
        )
    )
    session.flush()
