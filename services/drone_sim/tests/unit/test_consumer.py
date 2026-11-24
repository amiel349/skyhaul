"""The consumer's reaction to each kind of message (design note section 3), with a fake
channel: what is NACKed, what is dead-lettered, and that the ack comes last."""

import json
import logging
import threading
from typing import Any

import pytest
from sim_support.drones import board_with, make_drone
from sim_support.fakes import FakeBroker, FakeConnection
from sim_support.messages import DRONE, command, payload_of, raw_command
from skyhaul_contracts import Codec, CommandType, Message, MessageType
from skyhaul_drone_sim.board import JobsBoard, World
from skyhaul_drone_sim.clock import FakeClock
from skyhaul_drone_sim.consumer import Consumer, parse_routing_key
from skyhaul_drone_sim.outbox import Outbox, Outgoing, new_outbox
from skyhaul_drone_sim.registry import INTERNAL_ERROR_DETAIL, Registry
from skyhaul_drone_sim.replies import ack

CODEC = Codec()


def make_registry() -> Registry:
    registry = Registry()

    @registry.handles(CommandType.ARM)
    def handle_arm(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
        return [ack(msg)]

    @registry.handles(CommandType.LAND)
    def handle_land(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
        raise RuntimeError("land bug with secret detail")

    return registry


def make_consumer(
    outbox: Outbox | None = None, stop: threading.Event | None = None
) -> tuple[Consumer, Outbox, JobsBoard]:
    board = board_with()
    board.add_drone(make_drone(DRONE))
    outbox = outbox if outbox is not None else new_outbox(1000)
    consumer = Consumer(
        codec=CODEC,
        board=board,
        registry=make_registry(),
        outbox=outbox,
        clock=FakeClock(),
        stop=stop or threading.Event(),
        connect=FakeBroker().connect,
        connect_timeout_s=1.0,
        prefetch=32,
    )
    return consumer, outbox, board


def encoded(msg: Message[Any]) -> tuple[str, bytes]:
    return Codec.routing_key(msg.drone_id, msg.type.value), CODEC.encode(msg)


def deliver(consumer: Consumer, routing_key: str, body: bytes) -> FakeConnection:
    """Run one delivery through the pika callback on a fake channel."""
    connection = FakeConnection()
    channel = connection.channel()
    channel.basic_consume("drone_sim.cmd", consumer.on_message)
    connection.deliver(routing_key, body)
    connection.process_data_events()
    return connection


def nack_of(out: Outgoing) -> dict[str, Any]:
    assert out.message.type is MessageType.NACK
    assert out.persistent
    return payload_of(out)


# --- the happy path ---


def test_arm_is_handled_once_and_acked_with_its_correlation_id() -> None:
    consumer, _, _ = make_consumer()
    msg = command("ARM")

    outcome = consumer.handle(*encoded(msg))

    (reply,) = outcome.replies
    assert not outcome.dead_letter
    assert reply.message.type is MessageType.ACK
    assert reply.message.correlation_id == msg.correlation_id
    assert payload_of(reply)["command_msg_id"] == msg.msg_id


def test_ack_happens_only_after_the_replies_are_in_the_outbox() -> None:
    consumer, outbox, _ = make_consumer()
    seen_at_ack: list[int] = []
    connection = FakeConnection()
    channel = connection.channel()
    channel.on_ack = lambda tag: seen_at_ack.append(outbox.qsize())
    channel.basic_consume("drone_sim.cmd", consumer.on_message)

    for _ in range(3):
        connection.deliver(*encoded(command("ARM")))
    connection.process_data_events()

    assert channel.acks == [1, 2, 3]
    assert seen_at_ack == [1, 2, 3]  # each command's reply was already queued


def test_the_consumer_never_publishes() -> None:
    consumer, _, _ = make_consumer()

    connection = deliver(consumer, *encoded(command("ARM")))

    assert [name for name, _ in connection.calls if name == "basic_publish"] == []


# --- NACK paths ---


def test_unknown_type_gets_unknown_type_nack_with_the_request_ids_and_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    consumer, _, _ = make_consumer()
    key, body, envelope = raw_command("FLIP")

    with caplog.at_level(logging.WARNING):
        connection = deliver(consumer, key, body)

    channel = connection.channels[0]
    assert channel.acks == [1]
    assert channel.rejects == []
    reply = consumer.handle(key, body).replies[0]
    assert reply.message.correlation_id == envelope["correlation_id"]
    assert nack_of(reply)["reason"] == "UNKNOWN_TYPE"
    assert nack_of(reply)["command_msg_id"] == envelope["msg_id"]
    assert nack_of(reply)["command_type"] == "FLIP"
    assert any(r.levelno == logging.WARNING and "refused" in r.message for r in caplog.records)


def test_invalid_payload_gets_invalid_payload_nack() -> None:
    consumer, _, _ = make_consumer()
    key, body, envelope = raw_command("TAKEOFF", payload={"altitude_m": 9000.0})

    (reply,) = consumer.handle(key, body).replies

    assert nack_of(reply)["reason"] == "INVALID_PAYLOAD"
    assert "9000" not in nack_of(reply)["detail"]
    assert reply.message.correlation_id == envelope["correlation_id"]


def test_unsupported_version_for_the_routing_key_drone_gets_a_nack() -> None:
    consumer, _, _ = make_consumer()
    key, body, envelope = raw_command("ARM", schema_version=99)

    (reply,) = consumer.handle(key, body).replies

    assert nack_of(reply) == {
        "command_msg_id": envelope["msg_id"],
        "command_type": "ARM",
        "reason": "UNSUPPORTED_VERSION",
        "detail": "unsupported schema_version",
    }
    assert reply.message.drone_id == DRONE
    assert reply.message.correlation_id == envelope["correlation_id"]


def test_unsupported_version_never_echoes_the_version() -> None:
    consumer, _, _ = make_consumer()
    key, body, _ = raw_command("ARM", schema_version=10**300)

    (reply,) = consumer.handle(key, body).replies

    assert "1000" not in reply.message.model_dump_json()


@pytest.mark.parametrize(
    "routing_key",
    ["drone.main-d002.ARM", "drone.main-d001.TAKEOFF"],
    ids=["other-drone", "other-type"],
)
def test_unsupported_version_whose_hint_does_not_match_the_routing_key_is_dead_lettered(
    routing_key: str,
) -> None:
    consumer, _, _ = make_consumer()
    _, body, _ = raw_command("ARM", schema_version=99)

    outcome = consumer.handle(routing_key, body)

    assert outcome.dead_letter
    assert outcome.replies == []


def test_unsupported_version_without_a_readable_msg_id_is_dead_lettered() -> None:
    consumer, _, _ = make_consumer()
    key, body, _ = raw_command("ARM", schema_version=99, msg_id="not-a-uuid")

    assert consumer.handle(key, body).dead_letter


def test_raising_handler_gets_internal_error_nack_is_acked_and_traceback_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    consumer, outbox, _ = make_consumer()
    msg = command("LAND")

    with caplog.at_level(logging.ERROR):
        connection = deliver(consumer, *encoded(msg))

    channel = connection.channels[0]
    assert channel.acks == [1]  # acked: never redelivered
    assert channel.rejects == []
    assert channel.nacks == []
    reply = outbox.get_nowait()
    assert isinstance(reply, Outgoing)
    assert nack_of(reply)["reason"] == "INTERNAL_ERROR"
    assert nack_of(reply)["detail"] == INTERNAL_ERROR_DETAIL
    assert "secret detail" not in reply.message.model_dump_json()
    (record,) = [r for r in caplog.records if r.message == "command handler raised"]
    assert record.exc_info is not None
    assert record.correlation_id == msg.correlation_id  # type: ignore[attr-defined]


def test_unknown_drone_gets_no_reply_is_acked_and_counted() -> None:
    consumer, outbox, board = make_consumer()

    connection = deliver(consumer, *encoded(command("ARM", drone_id="main-d404")))

    assert connection.channels[0].acks == [1]
    assert outbox.empty()
    assert board.unknown_drone_commands() == 1


# --- dead-lettered ---


@pytest.mark.parametrize(
    "body",
    [b"\x00\xffgarbage", b"[]", b"{}", b"not json", b"{" * 100_000],
    ids=["binary", "array", "empty-object", "text", "deep"],
)
def test_undecodable_body_is_rejected_without_requeue(body: bytes) -> None:
    consumer, outbox, _ = make_consumer()

    connection = deliver(consumer, "drone.main-d001.ARM", body)

    channel = connection.channels[0]
    assert channel.rejects == [(1, False)]
    assert channel.acks == []
    assert outbox.empty()


@pytest.mark.parametrize(
    "routing_key",
    [
        "drone.main-d002.ARM",
        "drone.main-d001.TAKEOFF",
        "drone.main-d001",
        "drone.main-d001.ARM.x",
        "plane.main-d001.ARM",
    ],
)
def test_routing_key_that_does_not_match_the_envelope_is_dead_lettered(routing_key: str) -> None:
    consumer, _, _ = make_consumer()
    _, body = encoded(command("ARM"))

    assert consumer.handle(routing_key, body).dead_letter


@pytest.mark.parametrize("type", ["FLIP", "TAKEOFF"], ids=["unknown-type", "invalid-payload"])
def test_nackable_message_with_a_mismatched_routing_key_is_dead_lettered(type: str) -> None:
    consumer, _, _ = make_consumer()
    _, body, _ = raw_command(type, payload={"altitude_m": "high"})

    assert consumer.handle(f"drone.main-d002.{type}", body).dead_letter


@pytest.mark.parametrize(
    ("field", "value"),
    [("drone_id", "d" * 65), ("msg_id", "x" * 10_000), ("correlation_id", "c" * 10_000)],
)
def test_oversized_ids_are_dead_lettered_and_never_echoed(field: str, value: str) -> None:
    consumer, _, _ = make_consumer()
    key, _, envelope = raw_command("FLIP")
    envelope[field] = value

    outcome = consumer.handle(key, json.dumps(envelope).encode())

    assert outcome.dead_letter
    assert outcome.replies == []


def test_a_bug_while_handling_is_dead_lettered_instead_of_crashing_the_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumer, _, _ = make_consumer()

    def broken(routing_key: str, body: bytes) -> Any:
        raise KeyError("bug")

    monkeypatch.setattr(consumer, "handle", broken)
    connection = deliver(consumer, *encoded(command("ARM")))

    assert connection.channels[0].rejects == [(1, False)]


def test_garbage_unknown_type_and_raising_handler_then_arm_still_gets_its_ack() -> None:
    consumer, outbox, _ = make_consumer()
    connection = FakeConnection()
    channel = connection.channel()
    channel.basic_consume("drone_sim.cmd", consumer.on_message)
    arm = command("ARM")

    connection.deliver("drone.main-d001.ARM", b"\xde\xad")
    connection.deliver(*raw_command("FLIP")[:2])
    connection.deliver(*encoded(command("LAND")))
    connection.deliver(*encoded(arm))
    connection.process_data_events()

    replies = [outbox.get_nowait() for _ in range(outbox.qsize())]
    assert [r.message.type for r in replies if isinstance(r, Outgoing)] == [
        MessageType.NACK,
        MessageType.NACK,
        MessageType.ACK,
    ]
    last = replies[-1]
    assert isinstance(last, Outgoing)
    assert payload_of(last)["command_msg_id"] == arm.msg_id
    assert channel.rejects == [(1, False)]
    assert channel.acks == [2, 3, 4]


# --- stopping ---


def test_delivery_after_stop_is_left_unacked_for_requeue() -> None:
    stop = threading.Event()
    consumer, outbox, _ = make_consumer(stop=stop)
    stop.set()

    connection = deliver(consumer, *encoded(command("ARM")))

    channel = connection.channels[0]
    assert (channel.acks, channel.rejects, channel.nacks) == ([], [], [])
    assert outbox.empty()


def test_stop_while_the_outbox_is_full_leaves_the_command_unacked() -> None:
    stop = threading.Event()
    outbox = new_outbox(1)
    consumer, _, _ = make_consumer(outbox=outbox, stop=stop)
    connection = FakeConnection()
    channel = connection.channel()
    channel.basic_consume("drone_sim.cmd", consumer.on_message)
    connection.deliver(*encoded(command("ARM")))
    connection.deliver(*encoded(command("ARM")))
    timer = threading.Timer(0.2, stop.set)
    timer.start()

    connection.process_data_events()  # the second put blocks until stop is set

    timer.join()
    assert channel.acks == [1]
    assert outbox.qsize() == 1


def test_run_consumes_until_stopped_then_closes_its_channel() -> None:
    stop = threading.Event()
    broker = FakeBroker()
    board = board_with()
    board.add_drone(make_drone(DRONE))
    outbox = new_outbox(10)
    consumer = Consumer(
        codec=CODEC,
        board=board,
        registry=make_registry(),
        outbox=outbox,
        clock=FakeClock(),
        stop=stop,
        connect=broker.connect,
        connect_timeout_s=1.0,
        prefetch=32,
    )

    def prepare(connection: FakeConnection) -> None:
        connection.deliver(*encoded(command("ARM")))
        connection.on_poll = lambda: stop.set() if not connection.pending else None

    broker.prepare = prepare
    consumer.run()

    (connection,) = broker.connections
    assert connection.channels[0].acks == [1]
    assert connection.channels[0].closed
    assert not connection.is_open
    assert outbox.qsize() == 1


def test_parse_routing_key() -> None:
    assert parse_routing_key("drone.main-d001.ARM") == ("main-d001", "ARM")
    assert parse_routing_key("drone.a.b.c") is None
