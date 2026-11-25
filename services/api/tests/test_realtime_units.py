"""Unit tests of the realtime rules (#29): role-to-room mapping, event-to-room routing (from
the event name only), the sweep's stale-session rule, and the API's own command status
changes pushed as ``command.updated``."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from skyhaul_api.commands import service
from skyhaul_api.commands.publisher import Outcome
from skyhaul_api.realtime.fanout import EventFanout
from skyhaul_api.realtime.rooms import FLEET_ROOM, rooms_for, route
from skyhaul_api.realtime.socket import SocketSession, stale_sids
from skyhaul_fleet_core.models import Command, CommandStatus, Role

COMMAND_ROOMS = ("role:dispatcher", "role:admin")


@pytest.mark.parametrize("role", list(Role))
def test_every_role_joins_fleet_and_its_own_role_room_only(role: Role) -> None:
    assert rooms_for(role) == [FLEET_ROOM, f"role:{role.value}"]


@pytest.mark.parametrize(
    ("event", "rooms"),
    [
        ("drone.state", (FLEET_ROOM,)),
        ("alert.raised", (FLEET_ROOM,)),
        ("alert.updated", (FLEET_ROOM,)),
        ("mission.updated", (FLEET_ROOM,)),
        ("command.updated", COMMAND_ROOMS),
    ],
)
def test_the_room_comes_from_the_event_name(event: str, rooms: tuple[str, ...]) -> None:
    raw = json.dumps({"event": event, "data": {"room": "fleet", "to": "role:viewer"}})
    assert route(raw) == (event, {"room": "fleet", "to": "role:viewer"}, rooms)


def test_no_viewer_room_ever_gets_command_updated() -> None:
    routed = route(json.dumps({"event": "command.updated", "data": {}}))
    assert routed is not None
    assert not set(rooms_for(Role.VIEWER)) & set(routed[2])


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        "not json",
        json.dumps(["event", "data"]),
        json.dumps({"event": "join", "data": {}}),
        json.dumps({"event": "drone.state"}),
        json.dumps({"event": "drone.state", "data": [], "room": "role:admin"}),
        json.dumps({"event": "drone.state", "data": "text"}),
        json.dumps({"event": 1, "data": {}}),
    ],
)
def test_malformed_or_unknown_events_are_dropped(raw: Any) -> None:
    emitted: list[Any] = []
    fanout = EventFanout("redis://127.0.0.1:1/0", lambda *args: emitted.append(args))
    assert fanout.handle(raw) is False
    assert emitted == []


def test_the_sweep_picks_expired_and_inactive_sessions() -> None:
    now = datetime.now(UTC)
    active, disabled = uuid.uuid4(), uuid.uuid4()
    sessions = {
        "ok": SocketSession(active, now + timedelta(minutes=5)),
        "expired": SocketSession(active, now - timedelta(seconds=1)),
        "disabled": SocketSession(disabled, now + timedelta(minutes=5)),
    }
    assert sorted(stale_sids(sessions, {active}, now)) == ["disabled", "expired"]


class FakeSession:
    def commit(self) -> None:
        pass

    def refresh(self, _row: object) -> None:
        pass


def command_row() -> Command:
    return Command(
        id=uuid.uuid4(),
        drone_id="u-d001",
        type="ARM",
        payload={},
        correlation_id=uuid.uuid4(),
        status=CommandStatus.PENDING,
        requested_by=uuid.uuid4(),
        late=False,
        created_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    ("outcome", "moved", "pushes"),
    [(Outcome.CONFIRMED, True, 1), (Outcome.UNREACHABLE, True, 1), (Outcome.CONFIRMED, False, 0)],
    ids=["sent", "failed", "reply-overtook"],
)
def test_settle_pushes_command_updated_when_the_status_changed(
    monkeypatch: pytest.MonkeyPatch, outcome: Outcome, moved: bool, pushes: int
) -> None:
    pushed: list[tuple[str, dict[str, Any]]] = []
    targets: list[CommandStatus] = []

    def cas(_session: Any, _model: Any, _id: Any, target: CommandStatus, **_: Any) -> bool:
        targets.append(target)
        return moved

    monkeypatch.setattr(service, "cas_update", cas)
    monkeypatch.setattr(service.events, "push", lambda event, data: pushed.append((event, data)))
    command = command_row()
    service.settle(FakeSession(), command, outcome)  # type: ignore[arg-type]

    expected = CommandStatus.SENT if outcome is Outcome.CONFIRMED else CommandStatus.FAILED
    assert targets == [expected]
    assert [event for event, _ in pushed] == ["command.updated"] * pushes
    if pushes:
        assert pushed[0][1]["id"] == str(command.id)
