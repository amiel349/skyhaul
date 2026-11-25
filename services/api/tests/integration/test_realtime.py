"""Socket.IO ``/fleet`` against a served API app, real Postgres and Redis (#29): auth refusal,
server-assigned role rooms, client events ignored, the expiry sweep, and role-scoped events
from the Redis ``events`` channel. The clients are real python-socketio clients over
WebSocket."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import redis
import socketio
from flask import Flask
from skyhaul_api import create_app
from skyhaul_api.auth.tokens import TOKEN_TTL, issue_token
from skyhaul_api.commands.routes import PUBLISHER_KEY
from skyhaul_api.realtime import FANOUT_KEY, stop_realtime
from skyhaul_api.realtime.socket import NAMESPACE
from skyhaul_fleet_core.keys import EVENTS_CHANNEL
from skyhaul_fleet_core.models import Role, User
from sqlalchemy import update
from sqlalchemy.orm import Session
from werkzeug.serving import BaseWSGIServer, make_server

from .conftest import Stack

pytestmark = pytest.mark.integration

SWEEP_S = 1
DELIVERY_S = 2.0
ALL_EVENTS = ("drone.state", "alert.raised", "alert.updated", "mission.updated", "command.updated")


@dataclass
class Served:
    app: Flask
    url: str
    secret: str = field(repr=False)


@pytest.fixture(scope="module")
def served(stack: Stack) -> Iterator[Served]:
    config = stack.config(socket_sweep_s=SWEEP_S)
    app = create_app(config)
    server: BaseWSGIServer = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, name="test-server", daemon=True)
    thread.start()
    wait_for(lambda: app.extensions[FANOUT_KEY].subscribed, "the fan-out subscription")
    yield Served(app, f"http://127.0.0.1:{server.server_port}", config.jwt_secret)
    server.shutdown()
    stop_realtime(app)
    app.extensions[PUBLISHER_KEY].stop()


@dataclass
class Listener:
    client: socketio.Client
    received: list[tuple[str, Any]] = field(default_factory=list)
    disconnected: threading.Event = field(default_factory=threading.Event)

    def names(self) -> set[str]:
        return {name for name, _ in self.received}


def listen(url: str, auth: Any, query: str = "") -> Listener:
    """A connected client recording every server event. Raises on a refused connect."""
    client = socketio.Client(reconnection=False)
    listener = Listener(client)

    @client.on("*", namespace=NAMESPACE)
    def any_event(event: str, data: Any) -> None:
        listener.received.append((event, data))

    @client.on("disconnect", namespace=NAMESPACE)
    def on_disconnect(*_: Any) -> None:
        listener.disconnected.set()

    client.connect(
        url + query, namespaces=[NAMESPACE], auth=auth, transports=["websocket"], wait_timeout=5
    )
    return listener


def refusal(url: str, auth: Any, query: str = "") -> str:
    """The connect_error message of a refused connect."""
    client = socketio.Client(reconnection=False)
    errors: list[Any] = []
    client.on("connect_error", errors.append, namespace=NAMESPACE)
    with pytest.raises(socketio.exceptions.ConnectionError):
        client.connect(
            url + query, namespaces=[NAMESPACE], auth=auth, transports=["websocket"], wait_timeout=5
        )
    client.disconnect()
    assert errors, "no connect_error received"
    message: str = errors[0]["message"]
    return message


def token(served: Served, user: User, *, ttl: timedelta = TOKEN_TTL) -> str:
    issued = datetime.now(UTC) - TOKEN_TTL + ttl
    return issue_token(user.id, user.role.value, served.secret, now=issued)


def push(stack: Stack, event: str, data: dict[str, Any]) -> None:
    client = redis.Redis.from_url(stack.redis_url)
    client.publish(EVENTS_CHANNEL, json.dumps({"event": event, "data": data}))
    client.close()


def wait_for(check: Callable[[], bool], what: str, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.05)


def push_all(stack: Stack, marker: str) -> None:
    for event in ALL_EVENTS:
        push(stack, event, {"marker": marker, "event": event})


def wait_marker(listeners: list[Listener], marker: str, count: Callable[[Listener], int]) -> None:
    deadline = time.monotonic() + DELIVERY_S
    while time.monotonic() < deadline:
        if all(len(seen(x, marker)) == count(x) for x in listeners):
            break
        time.sleep(0.05)
    time.sleep(0.2)  # anything that should not arrive gets its chance to


def seen(listener: Listener, marker: str) -> list[str]:
    return [name for name, data in listener.received if data.get("marker") == marker]


@pytest.mark.parametrize(
    ("auth", "query", "reason"),
    [
        (None, "", "unauthorized"),
        ({"token": "not-a-jwt"}, "", "unauthorized"),
        ("expired", "", "token_expired"),
        (None, "?token=VALID", "unauthorized"),
        ("valid", "?token=VALID", "unauthorized"),
    ],
    ids=["no-token", "invalid", "expired", "query-only", "query-and-auth"],
)
def test_connect_is_refused_without_a_valid_auth_token(
    served: Served,
    make_user: Callable[[Role], User],
    auth: Any,
    query: str,
    reason: str,
) -> None:
    user = make_user(Role.ADMIN)
    valid = token(served, user)
    if auth == "expired":
        auth = {"token": token(served, user, ttl=timedelta(seconds=-5))}
    elif auth == "valid":
        auth = {"token": valid}
    assert refusal(served.url, auth, query.replace("VALID", valid)) == reason


def test_a_disabled_user_cannot_connect(
    served: Served, make_user: Callable[[Role], User], db_session: Session
) -> None:
    user = make_user(Role.DISPATCHER)
    db_session.execute(update(User).where(User.id == user.id).values(is_active=False))
    db_session.commit()
    assert refusal(served.url, {"token": token(served, user)}) == "unauthorized"


def test_events_reach_rooms_by_role_and_commands_only_dispatchers_and_admins(
    served: Served, stack: Stack, make_user: Callable[[Role], User]
) -> None:
    listeners = {
        role: listen(served.url, {"token": token(served, make_user(role))}) for role in Role
    }
    marker = f"m-{time.monotonic_ns()}"
    push_all(stack, marker)

    def expected(listener: Listener) -> int:
        return 4 if listener is listeners[Role.VIEWER] else 5

    wait_marker(list(listeners.values()), marker, expected)
    fleet_events = {"drone.state", "alert.raised", "alert.updated", "mission.updated"}
    assert set(seen(listeners[Role.VIEWER], marker)) == fleet_events
    assert set(seen(listeners[Role.DISPATCHER], marker)) == {*fleet_events, "command.updated"}
    assert set(seen(listeners[Role.ADMIN], marker)) == {*fleet_events, "command.updated"}
    # The payload is passed through as is (the REST shape the producer wrote).
    first = next(d for n, d in listeners[Role.VIEWER].received if n == "alert.raised")
    assert first == {"marker": marker, "event": "alert.raised"}
    for listener in listeners.values():
        listener.client.disconnect()


def test_client_events_are_ignored_and_a_viewer_cannot_join_the_command_rooms(
    served: Served, stack: Stack, make_user: Callable[[Role], User]
) -> None:
    viewer = listen(served.url, {"token": token(served, make_user(Role.VIEWER))})
    for room in ("role:dispatcher", "role:admin"):
        viewer.client.emit("join", {"room": room}, namespace=NAMESPACE)
        viewer.client.emit("join_room", room, namespace=NAMESPACE)
        viewer.client.emit("enter_room", room, namespace=NAMESPACE)
    viewer.client.emit("command.updated", {"forged": True}, namespace=NAMESPACE)
    # A payload naming a room does not redirect the emit either.
    marker = f"j-{time.monotonic_ns()}"
    push(stack, "command.updated", {"marker": marker, "room": "fleet", "to": "fleet"})
    push(stack, "drone.state", {"marker": marker})
    wait_marker([viewer], marker, lambda _: 1)
    assert seen(viewer, marker) == ["drone.state"]
    assert "command.updated" not in viewer.names()
    assert viewer.client.connected  # the server ignored the events, it did not crash
    viewer.client.disconnect()


def test_the_sweep_disconnects_expired_tokens_and_disabled_users(
    served: Served, make_user: Callable[[Role], User], db_session: Session
) -> None:
    short = listen(
        served.url, {"token": token(served, make_user(Role.VIEWER), ttl=timedelta(seconds=2))}
    )
    disabled_user = make_user(Role.DISPATCHER)
    disabled = listen(served.url, {"token": token(served, disabled_user)})
    steady = listen(served.url, {"token": token(served, make_user(Role.VIEWER))})

    db_session.execute(update(User).where(User.id == disabled_user.id).values(is_active=False))
    db_session.commit()

    assert short.disconnected.wait(2 + SWEEP_S * 3)
    assert disabled.disconnected.wait(SWEEP_S * 3)
    assert steady.client.connected
    # A reconnect authenticates again.
    assert refusal(served.url, {"token": token(served, disabled_user)}) == "unauthorized"
    steady.client.disconnect()
