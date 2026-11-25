"""Unit tests of the command service (#26): the publisher future always resolves and only the
publisher thread touches the channel; Codec errors become 422 field paths without values."""

from __future__ import annotations

import threading
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


class FakeChannel:
    """Records every call with the calling thread; ``basic_publish`` follows a script."""

    def __init__(self, script: list[BaseException | None]) -> None:
        self.script = script
        self.calls: list[tuple[str, str]] = []
        self.published: list[dict[str, Any]] = []
        self.is_open = True

    def _record(self, name: str) -> None:
        self.calls.append((name, threading.current_thread().name))

    def confirm_delivery(self) -> None:
        self._record("confirm_delivery")

    def basic_publish(self, exchange: str, routing_key: str, body: bytes, **kwargs: Any) -> None:
        self._record("basic_publish")
        self.published.append({"exchange": exchange, "routing_key": routing_key, **kwargs})
        step = self.script.pop(0) if self.script else None
        if step is not None:
            raise step


class FakeConnection:
    def __init__(self, channel: FakeChannel) -> None:
        self._channel, self.is_open = channel, True

    def channel(self) -> FakeChannel:
        self._channel._record("channel")
        return self._channel

    def process_data_events(self, time_limit: float) -> None:
        self._channel._record("process_data_events")

    def close(self) -> None:
        self.is_open = False


def takeoff() -> tuple[Any, bytes]:
    return build_message(codec, "TAKEOFF", DRONE, {"altitude_m": 30}, str(uuid.uuid4()))


def run_one(connect: Callable[[], Any], **kwargs: Any) -> Outcome:
    publisher = CommandPublisher(connect, **kwargs)
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
