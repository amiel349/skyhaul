"""The mission flow (m2-design sections 6 and 8): store the mission, then launch it through
the command service: ARM (only if Redis shows the drone LANDED) and START_MISSION, pipelined
without waiting, both with the mission's ``correlation_id`` and ``mission_id``.

The API only plans, launches, cancels and asks for an abort. ACTIVE, COMPLETED, ABORTED and
FAILED come from the drone, through the ingestor.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Final

from flask import current_app
from psycopg.errors import UniqueViolation
from skyhaul_contracts.codec import Codec
from skyhaul_fleet_core.missions import MISSION_UPDATED, failure_reason, mission_json
from skyhaul_fleet_core.models import Command, Mission, MissionEvent, MissionState
from skyhaul_fleet_core.transitions import cas_update
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from skyhaul_api import events
from skyhaul_api.audit.service import record
from skyhaul_api.commands import service as commands
from skyhaul_api.commands.publisher import CommandPublisher, Outcome
from skyhaul_api.commands.routes import CODEC_KEY, PUBLISHER_KEY
from skyhaul_api.drones.routes import live_states
from skyhaul_api.errors import ApiError

LIVE_INDEX: Final = "uq_missions_live_drone"
LAUNCH_TYPES: Final = ("ARM", "START_MISSION")
ABORT_ACTIONS: Final = ("RETURN_HOME", "LAND")


def mission_live() -> ApiError:
    return ApiError(409, "mission_live", "The drone already has a live mission.")


def illegal(state: MissionState) -> ApiError:
    return ApiError(409, "illegal_transition", f"Not allowed while the mission is {state.value}.")


def is_live_conflict(exc: IntegrityError) -> bool:
    diag = getattr(exc.orig, "diag", None)
    return isinstance(exc.orig, UniqueViolation) and (
        getattr(diag, "constraint_name", None) == LIVE_INDEX
    )


def find(session: Session, mission_id: uuid.UUID) -> Mission:
    mission = session.get(Mission, mission_id)
    if mission is None:
        raise ApiError(404, "not_found", "The mission does not exist.")
    return mission


def find_by_key(session: Session, user_id: uuid.UUID, key: str) -> Mission | None:
    return session.scalars(
        select(Mission).where(Mission.created_by == user_id, Mission.idempotency_key == key)
    ).first()


def replay(
    session: Session, existing: Mission, drone_id: str, waypoints: list[dict[str, Any]]
) -> tuple[Mission, list[Command]]:
    """The original mission and its launch commands. The same key for another mission: 409."""
    if existing.drone_id != drone_id or existing.waypoints != waypoints:
        raise ApiError(409, "idempotency_key_reused", "The key was used for another mission.")
    launched = session.scalars(
        select(Command)
        .where(Command.mission_id == existing.id, Command.type.in_(LAUNCH_TYPES))
        .order_by(Command.created_at)
    ).all()
    return existing, list(launched)


def create(
    session: Session,
    *,
    drone_id: str,
    waypoints: list[dict[str, Any]],
    start: bool,
    user_id: uuid.UUID,
    correlation_id: str,
    idempotency_key: str | None,
) -> Mission:
    """Insert the mission (PLANNED, or DISPATCHING with ``start``) and commit.
    Raises IntegrityError on a live mission or a concurrent use of the same key."""
    now = datetime.now(UTC)
    state = MissionState.DISPATCHING if start else MissionState.PLANNED
    mission = Mission(
        id=uuid.uuid4(),
        drone_id=drone_id,
        state=state,
        waypoints=waypoints,
        correlation_id=uuid.UUID(correlation_id),
        created_by=user_id,
        started_at=now if start else None,
        idempotency_key=idempotency_key,
    )
    session.add(mission)
    session.flush()
    add_event(session, mission, "created", {"state": state.value})
    _audit(session, "mission.create", mission, user_id, {"start": start})
    session.commit()
    return mission


def start(session: Session, mission: Mission, user_id: uuid.UUID) -> None:
    """PLANNED to DISPATCHING (a CAS), committed. 409 from any other state, or when the
    drone has another live mission."""
    try:
        moved = cas_update(
            session,
            Mission,
            mission.id,
            MissionState.DISPATCHING,
            started_at=datetime.now(UTC),
        )
    except IntegrityError as exc:
        session.rollback()
        if is_live_conflict(exc):
            raise mission_live() from None
        raise
    if not moved:
        session.rollback()
        raise illegal(mission.state)
    add_event(session, mission, "state_changed", {"state": MissionState.DISPATCHING.value})
    _audit(session, "mission.start", mission, user_id)
    session.commit()
    session.refresh(mission)


def cancel(session: Session, mission: Mission, user_id: uuid.UUID) -> None:
    moved = cas_update(
        session, Mission, mission.id, MissionState.CANCELLED, ended_at=datetime.now(UTC)
    )
    if not moved:
        session.rollback()
        raise illegal(mission.state)
    add_event(session, mission, "state_changed", {"state": MissionState.CANCELLED.value})
    _audit(session, "mission.cancel", mission, user_id)
    session.commit()
    session.refresh(mission)


def launch(session: Session, mission: Mission, user_id: uuid.UUID) -> list[Command]:
    """Send ARM (if the drone is LANDED) and START_MISSION in order, without waiting for
    replies. If START_MISSION never reached the broker the mission is FAILED and this is a
    503 ``command_not_sent``."""
    state = live_states([mission.drone_id])[0]
    payloads: list[tuple[str, dict[str, Any]]] = []
    if state is not None and state.get("flight_state") == "LANDED":
        payloads.append(("ARM", {}))
    payloads.append(
        ("START_MISSION", {"mission_id": str(mission.id), "waypoints": mission.waypoints})
    )
    sent, outcomes = _send(session, mission, user_id, payloads)
    if outcomes[-1] is not Outcome.CONFIRMED:
        fail(session, mission, "command_not_sent")
        raise commands.not_sent()
    return sent


def abort(session: Session, mission: Mission, user_id: uuid.UUID, action: str) -> Command:
    """Send RETURN_HOME or LAND for a live mission. It becomes ABORTED only when the drone's
    final status arrives (the ingestor)."""
    if mission.state not in (MissionState.DISPATCHING, MissionState.ACTIVE):
        raise illegal(mission.state)
    sent, outcomes = _send(session, mission, user_id, [(action, {})])
    if outcomes[0] is not Outcome.CONFIRMED:
        raise commands.not_sent()
    return sent[0]


def fail(session: Session, mission: Mission, reason: str) -> None:
    reason = failure_reason(reason)
    values = {"failure_reason": reason, "ended_at": datetime.now(UTC)}
    if cas_update(session, Mission, mission.id, MissionState.FAILED, **values):
        add_event(session, mission, "state_changed", {"state": "FAILED", "reason": reason})
    session.commit()
    session.refresh(mission)
    pushed(mission)


def add_event(session: Session, mission: Mission, kind: str, data: dict[str, Any]) -> None:
    session.add(MissionEvent(mission_id=mission.id, ts=datetime.now(UTC), kind=kind, data=data))


def pushed(mission: Mission) -> Mission:
    events.push(MISSION_UPDATED, mission_json(mission))
    return mission


def _send(
    session: Session,
    mission: Mission,
    user_id: uuid.UUID,
    payloads: list[tuple[str, dict[str, Any]]],
) -> tuple[list[Command], list[Outcome]]:
    codec: Codec = current_app.extensions[CODEC_KEY]
    publisher: CommandPublisher = current_app.extensions[PUBLISHER_KEY]
    built = [
        commands.build_message(codec, kind, mission.drone_id, payload, str(mission.correlation_id))
        for kind, payload in payloads
    ]
    stored: list[Command] = []
    for message, _ in built:
        add_event(
            session, mission, "command", {"command_id": message.msg_id, "type": message.type.value}
        )
        stored.append(
            commands.store(
                session, message, user_id=user_id, idempotency_key=None, mission_id=mission.id
            )
        )
    outcomes = commands.publish_all(publisher, built)
    settled = [
        commands.settle(session, command, outcome)
        for command, outcome in zip(stored, outcomes, strict=True)
    ]
    return settled, outcomes


def _audit(
    session: Session,
    action: str,
    mission: Mission,
    user_id: uuid.UUID,
    data: dict[str, Any] | None = None,
) -> None:
    record(
        session,
        action=action,
        outcome="success",
        actor=user_id,
        target_type="mission",
        target_id=str(mission.id),
        data={"drone_id": mission.drone_id, **(data or {})},
    )
