"""Mission state from telemetry, the dispatch timeout and the derived LINK_LOST, on real
Postgres, Redis and RabbitMQ as the least-privilege ingestor role (#28). The flight with the
simulator and the API is in services/api/tests/integration/test_missions.py.
"""

from __future__ import annotations

import json
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import redis
from pydantic import BaseModel
from skyhaul_contracts.codec import Codec
from skyhaul_contracts.message import Message
from skyhaul_contracts.types import MessageType
from skyhaul_contracts.v1 import HeartbeatPayload, MissionStatusPayload
from skyhaul_fleet_core.db import make_engine, make_session_factory
from skyhaul_fleet_core.keys import drone_state_key
from skyhaul_fleet_core.models import (
    Alert,
    AlertCode,
    Mission,
    MissionEvent,
    MissionState,
    Role,
    User,
)
from skyhaul_ingestor import store
from skyhaul_ingestor.watchdog import CommandSweeper
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from .conftest import Stack, StartIngestor, wait_until

pytestmark = pytest.mark.integration

codec = Codec()
type Publish = Callable[[Message[BaseModel] | bytes, str], None]


class Events:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def publish(self, events: list[dict[str, Any]]) -> None:
        self.published.extend(events)


def add_mission(engine: Engine, drone_id: str, state: MissionState, age_s: float = 0) -> uuid.UUID:
    with Session(engine) as session, session.begin():
        # Never logs in: the hash is a random placeholder, not a credential.
        user = User(
            email=f"u-{uuid.uuid4().hex[:8]}@example.com",
            password_hash=secrets.token_hex(16),
            role=Role.DISPATCHER,
        )
        session.add(user)
        session.flush()
        mission = Mission(
            drone_id=drone_id,
            state=state,
            waypoints=[{"lat": 32.0, "lon": 34.8, "altitude_m": 10}],
            correlation_id=uuid.uuid4(),
            created_by=user.id,
            started_at=datetime.now(UTC) - timedelta(seconds=age_s),
        )
        session.add(mission)
        session.flush()
        return mission.id


def mission(engine: Engine, mission_id: uuid.UUID) -> Mission:
    with Session(engine) as session:
        row = session.get(Mission, mission_id)
        assert row is not None
        return row


def status(drone_id: str, mission_id: uuid.UUID, state: str, progress: float) -> Any:
    payload = MissionStatusPayload(
        mission_id=str(mission_id),
        state=state,
        waypoint_index=0 if progress < 100 else 1,
        waypoints_total=1,
        progress_pct=progress,
    )
    return codec.new(MessageType.MISSION_STATUS, drone_id, payload, str(uuid.uuid4()))


def heartbeat(drone_id: str) -> Any:
    payload = HeartbeatPayload(flight_state="LANDED", armed=False, uptime_s=3)
    return codec.new(MessageType.HEARTBEAT, drone_id, payload, None)


def test_mission_status_moves_the_mission_forward_only_and_strangers_are_ignored(
    engine: Engine,
    drone_id: str,
    register: Callable[[str], None],
    start_ingestor: StartIngestor,
    publish: Publish,
) -> None:
    register(drone_id)
    other = f"it{secrets.token_hex(4)}-d002"
    register(other)
    mission_id = add_mission(engine, drone_id, MissionState.DISPATCHING)
    running = start_ingestor()

    # ACTIVE telemetry before any START_MISSION ACK still makes it ACTIVE, with progress.
    publish(status(drone_id, mission_id, "ACTIVE", 40.0), "")
    wait_until(lambda: mission(engine, mission_id).state is MissionState.ACTIVE, what="ACTIVE")
    wait_until(lambda: mission(engine, mission_id).progress_pct == 40.0, what="progress")
    publish(status(drone_id, mission_id, "COMPLETED", 100.0), "")
    wait_until(lambda: mission(engine, mission_id).state is MissionState.COMPLETED, what="done")

    # Out of order, another drone's, or an unknown mission: nothing changes, counted.
    publish(status(drone_id, mission_id, "ACTIVE", 50.0), "")
    publish(status(other, mission_id, "ABORTED", 50.0), "")
    publish(status(drone_id, uuid.uuid4(), "ACTIVE", 10.0), "")
    wait_until(lambda: running.counters["mission_status_ignored"] >= 2, what="ignored")
    final = mission(engine, mission_id)
    assert (final.state, final.progress_pct, final.ended_at is not None) == (
        MissionState.COMPLETED,
        100.0,
        True,
    )
    with Session(engine) as session:
        kinds = session.scalars(
            select(MissionEvent.data).where(MissionEvent.mission_id == mission_id)
        ).all()
    assert [d["state"] for d in kinds] == ["ACTIVE", "COMPLETED"]


def test_a_mission_without_active_for_60_s_fails_with_the_reason(
    stack: Stack, engine: Engine, drone_id: str, register: Callable[[str], None]
) -> None:
    register(drone_id)
    stale = add_mission(engine, drone_id, MissionState.DISPATCHING, age_s=61)
    sessions = make_session_factory(make_engine(stack.ingestor_url, pool_size=1))
    events = Events()
    sweeper = CommandSweeper(sessions, events)  # type: ignore[arg-type]

    assert sweeper.maybe_run(0.0) >= 1
    failed = mission(engine, stale)
    assert (failed.state, failed.failure_reason) == (MissionState.FAILED, store.DISPATCH_TIMEOUT)
    assert any(
        e["event"] == "mission.updated" and e["data"]["id"] == str(stale) for e in events.published
    )


def test_silence_raises_one_link_lost_and_the_next_heartbeat_clears_it(
    stack: Stack,
    engine: Engine,
    drone_id: str,
    register: Callable[[str], None],
    start_ingestor: StartIngestor,
    publish: Publish,
    redis_client: redis.Redis,
) -> None:
    register(drone_id)
    quiet = f"it{secrets.token_hex(4)}-d009"  # registered, never sends a heartbeat
    register(quiet)
    start_ingestor(link_loss_after_s=1)

    def open_alerts(drone: str) -> list[Alert]:
        with Session(engine) as session:
            return list(
                session.scalars(
                    select(Alert).where(Alert.drone_id == drone, Alert.cleared_at.is_(None))
                )
            )

    def link() -> Any:
        return json.loads(redis_client.hget(drone_state_key(drone_id), "link") or "null")

    publish(heartbeat(drone_id), "")
    lost = wait_until(lambda: open_alerts(drone_id), what="LINK_LOST")
    assert [(a.code, a.severity.value, a.source.value) for a in lost] == [
        (AlertCode.LINK_LOST, "WARNING", "DERIVED")
    ]
    wait_until(lambda: link() == "LOST", what="link LOST")
    # A second watchdog (or a restart) cannot raise a second open one.
    sessions = make_session_factory(make_engine(stack.ingestor_url, pool_size=1))
    with sessions.begin() as session:
        assert store.raise_link_lost(session, drone_id, datetime.now(UTC)) is None

    publish(heartbeat(drone_id), "")
    wait_until(lambda: not open_alerts(drone_id), what="LINK_LOST cleared")
    wait_until(lambda: link() == "UP", what="link UP")
    assert open_alerts(quiet) == []
