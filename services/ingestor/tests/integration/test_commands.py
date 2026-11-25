"""The reply CAS and the command sweeper on a real Postgres, as the least-privilege ingestor
role (#26). The full flow (API, broker, simulator) is in services/api/tests/integration.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from skyhaul_fleet_core.db import make_engine, make_session_factory
from skyhaul_fleet_core.models import Command, CommandStatus, Role, User
from skyhaul_ingestor import store
from skyhaul_ingestor.handlers import Reply
from skyhaul_ingestor.watchdog import CommandSweeper
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from .conftest import Stack

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)


class Events:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def publish(self, events: list[dict[str, Any]]) -> None:
        self.published.extend(events)


def add_command(engine: Engine, drone_id: str, status: CommandStatus, age_s: float) -> uuid.UUID:
    with Session(engine) as session, session.begin():
        email = f"u-{uuid.uuid4().hex[:8]}@example.com"
        # Never logs in: the hash is a random placeholder, not a credential.
        user = User(email=email, password_hash=secrets.token_hex(16), role=Role.DISPATCHER)
        session.add(user)
        session.flush()
        command = Command(
            id=uuid.uuid4(),
            drone_id=drone_id,
            type="ARM",
            payload={},
            correlation_id=uuid.uuid4(),
            status=status,
            requested_by=user.id,
            created_at=NOW - timedelta(seconds=age_s),
            sent_at=NOW - timedelta(seconds=age_s) if status is CommandStatus.SENT else None,
        )
        session.add(command)
        return command.id


def status_of(engine: Engine, command_id: uuid.UUID) -> tuple[CommandStatus, bool]:
    with Session(engine) as session:
        row = session.get(Command, command_id)
        assert row is not None
        return row.status, row.late


def test_sweep_times_out_sent_fails_pending_and_a_late_reply_wins_once(
    stack: Stack, engine: Engine, drone_id: str, register: Callable[[str], None]
) -> None:
    register(drone_id)
    old_sent = add_command(engine, drone_id, CommandStatus.SENT, age_s=11)
    fresh_sent = add_command(engine, drone_id, CommandStatus.SENT, age_s=1)
    old_pending = add_command(engine, drone_id, CommandStatus.PENDING, age_s=31)
    sessions = make_session_factory(make_engine(stack.ingestor_url, pool_size=1))
    events = Events()
    sweeper = CommandSweeper(sessions, events, wall_clock=lambda: NOW)  # type: ignore[arg-type]

    assert sweeper.maybe_run(0.0) == 2
    assert status_of(engine, old_sent) == (CommandStatus.TIMED_OUT, False)
    assert status_of(engine, fresh_sent) == (CommandStatus.SENT, False)
    assert status_of(engine, old_pending) == (CommandStatus.FAILED, False)
    assert {e["data"]["id"] for e in events.published} == {str(old_sent), str(old_pending)}

    def reply(command_id: uuid.UUID, status: CommandStatus, drone: str = drone_id) -> bool:
        with sessions.begin() as session:
            moved = store.apply_reply(session, Reply(command_id, drone, status, NOW))
            return moved is not None

    # A late ACK wins with late=true; a duplicate or a later NACK changes nothing.
    assert reply(old_sent, CommandStatus.ACKED)
    assert not reply(old_sent, CommandStatus.ACKED)
    assert not reply(old_sent, CommandStatus.NACKED)
    assert status_of(engine, old_sent) == (CommandStatus.ACKED, True)
    # An ACK before the sweep is not late, and the sweep then leaves it alone.
    assert not reply(fresh_sent, CommandStatus.ACKED, drone="other-d001")
    assert reply(fresh_sent, CommandStatus.ACKED)
    later = NOW + timedelta(seconds=60)
    sweeper = CommandSweeper(sessions, events, wall_clock=lambda: later)  # type: ignore[arg-type]
    sweeper.maybe_run(0.0)
    assert status_of(engine, fresh_sent) == (CommandStatus.ACKED, False)
    # FAILED is not final for replies: the broker may have routed it after the deadline.
    # A late NACK wins with late=true, and nothing moves it afterwards (#28, PR #34 review).
    assert reply(old_pending, CommandStatus.NACKED)
    assert status_of(engine, old_pending) == (CommandStatus.NACKED, True)
    assert not reply(old_pending, CommandStatus.ACKED)
