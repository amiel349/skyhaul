"""The publisher with a fake connection: the only sender, FIFO, confirms, reconnect,
heartbeats while idle, and the shutdown drain bound."""

import logging
import threading
import time

import pytest
from pika.exceptions import ConnectionClosedByBroker, StreamLostError
from sim_support.config import always_current
from sim_support.fakes import FakeBroker, FakeConnection
from sim_support.messages import DRONE, command, outgoing
from skyhaul_contracts import Codec
from skyhaul_drone_sim.outbox import SENTINEL, Outbox, Outgoing, new_outbox
from skyhaul_drone_sim.publisher import Publisher, PublisherThreadError
from skyhaul_drone_sim.replies import ack

DEADLINE_S = 5.0


def make_publisher(
    broker: FakeBroker, outbox: Outbox, stop: threading.Event, drain_s: float = 5.0
) -> Publisher:
    return Publisher(
        codec=Codec(),
        outbox=outbox,
        stop=stop,
        is_current=always_current,
        connect=broker.connect,
        connect_timeout_s=1.0,
        drain_s=drain_s,
    )


def wait_for(condition, timeout_s: float = DEADLINE_S) -> None:  # type: ignore[no-untyped-def]
    """Poll `condition` until it holds; fail at the deadline."""
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.01)


def published(broker: FakeBroker) -> list[bytes]:
    return [p.body for c in broker.connections for ch in c.channels for p in ch.published]


def stop_publisher(publisher: Publisher, outbox: Outbox, stop: threading.Event) -> None:
    stop.set()
    outbox.put(SENTINEL)
    publisher.join(DEADLINE_S)
    assert not publisher.is_alive()


def test_every_message_is_sent_from_the_publisher_thread_only_with_confirms() -> None:
    broker, outbox, stop = FakeBroker(), new_outbox(100), threading.Event()
    publisher = make_publisher(broker, outbox, stop)
    publisher.start()
    for n in range(20):
        outbox.put(outgoing(n=n))
    outbox.put(ack(command("ARM")))

    stop_publisher(publisher, outbox, stop)

    (connection,) = broker.connections
    (channel,) = connection.channels
    assert channel.confirms
    assert len(channel.published) == 21
    assert {p.thread.name for p in channel.published} == {"publisher"}
    assert {t.name for name, t in connection.calls} == {"publisher"}
    assert all(p.mandatory is False for p in channel.published)


def test_messages_leave_in_fifo_order_with_the_right_exchange_key_and_persistence() -> None:
    broker, outbox, stop = FakeBroker(), new_outbox(100), threading.Event()
    publisher = make_publisher(broker, outbox, stop)
    reply = ack(command("ARM"))
    heartbeats = [outgoing(n=n) for n in range(3)]
    for item in [heartbeats[0], reply, heartbeats[1], heartbeats[2]]:
        outbox.put(item)
    publisher.start()

    stop_publisher(publisher, outbox, stop)

    sent = broker.connections[0].channels[0].published
    codec = Codec()
    assert [codec.decode(p.body).msg_id for p in sent] == [
        heartbeats[0].message.msg_id,
        reply.message.msg_id,
        heartbeats[1].message.msg_id,
        heartbeats[2].message.msg_id,
    ]
    assert [p.exchange for p in sent] == ["fleet.telemetry"] * 4
    assert sent[1].routing_key == Codec.routing_key(DRONE, "ACK")
    assert [p.properties.delivery_mode for p in sent] == [1, 2, 1, 1]


def test_publishing_from_another_thread_is_refused() -> None:
    broker, outbox, stop = FakeBroker(), new_outbox(10), threading.Event()
    publisher = make_publisher(broker, outbox, stop)

    with pytest.raises(PublisherThreadError):
        publisher.send(outgoing())


def test_lost_connection_is_reopened_and_the_waiting_message_is_still_sent() -> None:
    broker, outbox, stop = FakeBroker(), new_outbox(100), threading.Event()
    broker.prepare = lambda c: (
        c.publish_errors.append(StreamLostError("gone")) if not broker.connections else None
    )
    publisher = make_publisher(broker, outbox, stop)
    messages = [outgoing(n=n) for n in range(5)]
    for out in messages:
        outbox.put(out)
    publisher.start()

    stop_publisher(publisher, outbox, stop)

    assert len(broker.connections) == 2
    codec = Codec()
    assert [codec.decode(b).msg_id for b in published(broker)] == [
        out.message.msg_id for out in messages
    ]
    assert publisher.dropped == 0


def test_idle_publisher_services_its_connection_and_reconnects_if_it_dropped() -> None:
    broker, outbox, stop = FakeBroker(), new_outbox(10), threading.Event()
    broker.prepare = lambda c: (
        c.keepalive_errors.append(ConnectionClosedByBroker(320, "forced"))
        if not broker.connections
        else None
    )
    publisher = make_publisher(broker, outbox, stop)
    publisher.start()

    # Two idle periods: the first keep-alive finds the connection closed and reopens it.
    wait_for(lambda: len(broker.connections) == 2)
    second: FakeConnection = broker.connections[1]
    wait_for(lambda: any(name == "process_data_events" for name, _ in second.calls))
    outbox.put(outgoing())
    stop_publisher(publisher, outbox, stop)

    assert len(published(broker)) == 1


def test_unencodable_message_is_dropped_and_counted_and_the_next_one_still_goes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broker, outbox, stop = FakeBroker(), new_outbox(10), threading.Event()
    codec = Codec()
    publisher = Publisher(
        codec=codec,
        outbox=outbox,
        stop=stop,
        is_current=always_current,
        connect=broker.connect,
        connect_timeout_s=1.0,
        drain_s=5.0,
    )
    good = outgoing()
    bad = Outgoing(good.message.model_copy(update={"drone_id": "BAD ID"}), persistent=False)
    outbox.put(bad)
    outbox.put(good)
    publisher.start()

    with caplog.at_level(logging.ERROR):
        stop_publisher(publisher, outbox, stop)

    assert len(published(broker)) == 1
    assert publisher.dropped == 1
    assert codec.stats().encode_errors == 1


def test_on_stop_the_outbox_is_drained_up_to_the_sentinel() -> None:
    broker, outbox, stop = FakeBroker(), new_outbox(2000), threading.Event()
    publisher = make_publisher(broker, outbox, stop)
    for n in range(1000):
        outbox.put(outgoing(n=n))
    stop.set()
    outbox.put(SENTINEL)

    publisher.start()
    publisher.join(DEADLINE_S)

    assert not publisher.is_alive()
    assert publisher.published == 1000
    assert publisher.dropped == 0


def test_messages_left_after_the_drain_bound_are_logged_as_dropped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broker, outbox, stop = FakeBroker(), new_outbox(100), threading.Event()
    gate = threading.Event()
    broker.prepare = lambda c: setattr(c, "publish_gate", gate)
    publisher = make_publisher(broker, outbox, stop, drain_s=0.3)
    for n in range(10):
        outbox.put(outgoing(n=n))
    publisher.start()
    wait_for(lambda: outbox.qsize() == 9)  # the first message is stuck in a slow publish

    stop.set()
    publisher.begin_drain()
    started = time.monotonic()
    timer = threading.Timer(0.5, gate.set)  # the broker answers after the deadline
    timer.start()
    with caplog.at_level(logging.ERROR):
        publisher.join(DEADLINE_S)
    timer.join()

    assert not publisher.is_alive()
    assert time.monotonic() - started < 2.0
    assert publisher.published == 1
    assert publisher.dropped == 9
    (record,) = [r for r in caplog.records if "drain deadline passed" in r.message]
    assert record.dropped == 9  # type: ignore[attr-defined]


def test_publisher_that_cannot_connect_reports_failure() -> None:
    from skyhaul_drone_sim.amqp import BrokerUnavailable

    def refuse(timeout_s: float, stop: threading.Event | None) -> FakeConnection:
        raise BrokerUnavailable("no broker")

    publisher = Publisher(
        codec=Codec(),
        outbox=new_outbox(1),
        stop=threading.Event(),
        is_current=always_current,
        connect=refuse,
        connect_timeout_s=0.1,
        drain_s=1.0,
    )
    publisher.start()

    assert publisher.ready.wait(DEADLINE_S)
    publisher.join(DEADLINE_S)
    assert publisher.failed


def test_a_message_the_broker_keeps_refusing_is_dropped_while_running_and_the_next_one_goes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pika.exceptions import NackError
    from skyhaul_drone_sim import publisher as publisher_module

    monkeypatch.setattr(publisher_module, "NACK_BACKOFF_S", 0.0, raising=False)
    broker, outbox, stop = FakeBroker(), new_outbox(10), threading.Event()
    attempts = getattr(publisher_module, "NACK_ATTEMPTS", 5)
    broker.prepare = lambda c: c.publish_errors.extend(NackError([]) for _ in range(attempts))
    publisher = make_publisher(broker, outbox, stop)
    publisher.start()
    outbox.put(outgoing(n=1))
    outbox.put(outgoing(n=2))

    wait_for(lambda: len(published(broker)) == 1)

    assert not stop.is_set()
    assert publisher.is_alive()
    assert publisher.dropped == 1
    assert publisher.published == 1
    stop_publisher(publisher, outbox, stop)


def test_a_message_nacked_fewer_times_than_the_cap_is_still_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pika.exceptions import NackError
    from skyhaul_drone_sim import publisher as publisher_module

    monkeypatch.setattr(publisher_module, "NACK_BACKOFF_S", 0.0, raising=False)
    broker, outbox, stop = FakeBroker(), new_outbox(10), threading.Event()
    retries = getattr(publisher_module, "NACK_ATTEMPTS", 5) - 1
    broker.prepare = lambda c: c.publish_errors.extend(NackError([]) for _ in range(retries))
    publisher = make_publisher(broker, outbox, stop)
    publisher.start()
    outbox.put(outgoing())

    wait_for(lambda: len(published(broker)) == 1)

    assert publisher.dropped == 0
    stop_publisher(publisher, outbox, stop)


def test_a_message_the_broker_keeps_refusing_is_dropped_at_the_drain_deadline() -> None:
    from pika.exceptions import NackError

    broker, outbox, stop = FakeBroker(), new_outbox(10), threading.Event()
    broker.prepare = lambda c: c.publish_errors.extend(NackError([]) for _ in range(100_000))
    publisher = make_publisher(broker, outbox, stop, drain_s=0.2)
    outbox.put(outgoing())
    stop.set()
    publisher.begin_drain()

    publisher.start()
    publisher.join(DEADLINE_S)

    assert not publisher.is_alive()
    assert publisher.dropped == 1
