"""Unit tests of the command service (#26): the publisher future always resolves and only the
publisher thread touches the channel; Codec errors become 422 field paths without values."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import pika
import pytest
from pika.exceptions import AMQPConnectionError, NackError, UnroutableError
from skyhaul_api.commands import publisher as publisher_module
from skyhaul_api.commands.publisher import CommandPublisher, Outcome
from skyhaul_api.commands.service import build_message
from skyhaul_api.errors import ApiError
from skyhaul_contracts.codec import Codec
from skyhaul_contracts.topology import COMMAND_EXCHANGE

codec = Codec()
DRONE = "u-d001"


HANG = "hang"
"""A ``basic_publish`` script step: the broker never confirms (waits for the deadline timer)."""


class FakeChannel:
    """Records every call with the calling thread; ``basic_publish`` follows a script."""

    def __init__(self, script: list[Any]) -> None:
        self.script = script
        self.calls: list[tuple[str, str]] = []
        self.published: list[dict[str, Any]] = []
        self.is_open = True
        self.connection: FakeConnection | None = None

    def _record(self, name: str) -> None:
        self.calls.append((name, threading.current_thread().name))

    def confirm_delivery(self) -> None:
        self._record("confirm_delivery")

    def basic_publish(self, exchange: str, routing_key: str, body: bytes, **kwargs: Any) -> None:
        self._record("basic_publish")
        self.published.append({"exchange": exchange, "routing_key": routing_key, **kwargs})
        if self.connection is None or not self.connection.is_open:
            raise AMQPConnectionError("aborted")
        step = self.script.pop(0) if self.script else None
        if step == HANG:
            # Like pika's confirm wait: it polls the I/O loop, which runs the due timers.
            self.connection.wait_for_timer()
            raise AMQPConnectionError("stream lost")
        if step is not None:
            raise step


class FakeConnection:
    def __init__(self, channel: FakeChannel) -> None:
        self._channel, self.is_open = channel, True
        channel.connection = self
        self.timer: tuple[float, Callable[[], None]] | None = None

    def wait_for_timer(self) -> None:
        assert self.timer is not None, "no deadline timer armed during the confirm wait"
        delay, on_expire = self.timer
        time.sleep(max(delay, 0.0))
        on_expire()

    def channel(self) -> FakeChannel:
        self._channel._record("channel")
        return self._channel

    def process_data_events(self, time_limit: float) -> None:
        self._channel._record("process_data_events")

    def close(self) -> None:
        self.is_open = False


def fake_timer(
    connection: FakeConnection, delay_s: float, on_expire: Callable[[], None]
) -> Callable[[], None]:
    connection.timer = (delay_s, on_expire)

    def cancel() -> None:
        connection.timer = None

    return cancel


def fake_abort(connection: FakeConnection) -> None:
    connection.is_open = False


def takeoff() -> tuple[Any, bytes]:
    return build_message(codec, "TAKEOFF", DRONE, {"altitude_m": 30}, str(uuid.uuid4()))


def run_one(connect: Callable[[], Any], **kwargs: Any) -> Outcome:
    publisher = CommandPublisher(connect, timer=fake_timer, abort=fake_abort, **kwargs)
    publisher.start()
    try:
        return publisher.submit(*takeoff()).result(timeout=5)
    finally:
        publisher.stop()


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ([None], Outcome.CONFIRMED),
        ([UnroutableError([])], Outcome.UNROUTABLE),
        ([NackError([])] * publisher_module.NACK_ATTEMPTS, Outcome.NACKED),
        ([NackError([]), None], Outcome.CONFIRMED),
    ],
)
def test_every_broker_answer_resolves_the_future_on_the_publisher_thread_only(
    script: list[BaseException | None], expected: Outcome
) -> None:
    channel = FakeChannel(list(script))
    assert run_one(lambda: FakeConnection(channel)) is expected
    assert {thread for _, thread in channel.calls} == {"command-publisher"}
    first = channel.published[0]
    assert (first["exchange"], first["routing_key"], first["mandatory"]) == (
        COMMAND_EXCHANGE,
        f"drone.{DRONE}.TAKEOFF",
        True,
    )
    assert first["properties"].delivery_mode == pika.DeliveryMode.Persistent.value


def test_an_unreachable_broker_and_a_passed_deadline_still_resolve_the_future() -> None:
    def refuse() -> Any:
        raise AMQPConnectionError("down")

    assert run_one(refuse) is Outcome.UNREACHABLE

    channel = FakeChannel([])
    assert run_one(lambda: FakeConnection(channel), deadline_s=0.0) is Outcome.EXPIRED
    assert not channel.published  # never sent once the deadline has passed


def test_a_stopped_publisher_resolves_at_once_and_send_refuses_other_threads() -> None:
    publisher = CommandPublisher(lambda: None)
    assert publisher.submit(*takeoff()).result(timeout=0) is Outcome.STOPPED
    assert not publisher.connected
    with pytest.raises(publisher_module.PublisherThreadError):
        publisher.send(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("type_name", "payload", "field"),
    [
        ("TAKEOFF", {"altitude_m": 987654}, "payload.altitude_m"),
        ("TAKEOFF", {}, "payload.altitude_m"),
        ("GOTO", {"lat": "secret-input", "lon": 34.8, "altitude_m": 30}, "payload.lat"),
    ],
)
def test_contract_violations_are_422_with_field_paths_and_no_input_values(
    type_name: str, payload: dict[str, Any], field: str
) -> None:
    with pytest.raises(ApiError) as raised:
        build_message(codec, type_name, DRONE, payload, str(uuid.uuid4()))
    error = raised.value
    assert (error.status, error.error) == (422, "validation_failed")
    assert error.fields is not None
    assert field in error.fields
    text = repr(error.fields)
    assert "987654" not in text
    assert "secret-input" not in text


def test_a_valid_command_gets_a_fresh_msg_id_and_the_request_correlation_id() -> None:
    correlation_id = str(uuid.uuid4())
    first, body = build_message(codec, "ARM", DRONE, {}, correlation_id)
    second, _ = build_message(codec, "ARM", DRONE, {}, correlation_id)
    assert first.msg_id != second.msg_id
    assert first.correlation_id == correlation_id
    assert codec.decode(body) == first


def test_a_broker_that_never_confirms_resolves_expired_within_the_deadline_and_sends_no_more() -> (
    None
):
    channel = FakeChannel([HANG])
    connections: list[FakeConnection] = []

    def connect() -> FakeConnection:
        connections.append(FakeConnection(channel))
        return connections[-1]

    publisher = CommandPublisher(connect, deadline_s=0.3, timer=fake_timer, abort=fake_abort)
    publisher.start()
    try:
        started = time.monotonic()
        outcome = publisher.submit(*takeoff()).result(timeout=publisher.deadline_s + 1)
        elapsed = time.monotonic() - started
        time.sleep(0.6)  # past the deadline: no retry, no reconnect-and-resend
    finally:
        publisher.stop()

    assert outcome is Outcome.EXPIRED  # the service maps it to FAILED and 503
    assert elapsed < publisher.deadline_s + 0.5
    assert len(channel.published) == 1
    assert not connections[0].is_open  # aborted: no confirm can arrive afterwards
