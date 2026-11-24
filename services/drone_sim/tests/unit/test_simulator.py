"""The wired simulator on fake connections: exactly three core threads, only the publisher
publishes, and the stop event shuts everything down."""

import secrets
import threading

from sim_support.fakes import FakeBroker, FakeConnection
from sim_support.messages import DRONE, command
from skyhaul_contracts import Codec, MessageType
from skyhaul_drone_sim.config import SimConfig
from skyhaul_drone_sim.handlers import registry
from skyhaul_drone_sim.simulator import EXIT_BROKER_UNAVAILABLE, EXIT_OK, Simulator

CFG = SimConfig(
    rabbitmq_host="unused",
    rabbitmq_user="unused",
    rabbitmq_password=secrets.token_hex(8),
    drones=(DRONE,),
)


def encoded_arm() -> tuple[str, bytes]:
    return f"drone.{DRONE}.ARM", Codec().encode(command("ARM"))


def encoded_set_speed() -> tuple[str, bytes]:
    """A command a drone accepts in any state, as often as it is sent."""
    return f"drone.{DRONE}.SET_SPEED", Codec().encode(command("SET_SPEED"))


def test_running_simulator_has_exactly_the_three_core_threads() -> None:
    consumers, publishers = FakeBroker(), FakeBroker()
    sim = Simulator(
        CFG,
        registry=registry,
        consumer_connect=consumers.connect,
        publisher_connect=publishers.connect,
    )
    seen: list[set[str]] = []

    def prepare(connection: FakeConnection) -> None:
        def poll() -> None:
            seen.append({t.name for t in threading.enumerate()})
            sim.stop()

        connection.on_poll = poll

    consumers.prepare = prepare
    me = threading.current_thread()
    name, me.name = me.name, "consumer"  # as __main__ does
    before = set(threading.enumerate())
    before_names = {t.name for t in before}
    try:
        assert sim.run() == EXIT_OK
    finally:
        me.name = name

    (during,) = seen
    assert during == before_names | {"scheduler", "publisher"}
    assert len(during) == len(before) + 2  # the calling thread is the consumer
    assert set(threading.enumerate()) == before


def test_commands_are_answered_from_the_publisher_thread_only_and_drained_on_stop() -> None:
    consumers, publishers = FakeBroker(), FakeBroker()
    sim = Simulator(
        CFG,
        registry=registry,
        consumer_connect=consumers.connect,
        publisher_connect=publishers.connect,
    )

    def prepare(connection: FakeConnection) -> None:
        for _ in range(50):
            connection.deliver(*encoded_set_speed())
        connection.on_poll = lambda: sim.stop() if not connection.pending else None

    consumers.prepare = prepare

    assert sim.run() == EXIT_OK

    (consumer_connection,) = consumers.connections
    assert "basic_publish" not in {name for name, _ in consumer_connection.calls}
    assert consumer_connection.channels[0].acks == list(range(1, 51))
    sent = [p for c in publishers.connections for ch in c.channels for p in ch.published]
    assert len(sent) == 50
    assert {p.thread.name for p in sent} == {"publisher"}
    assert {Codec().decode(p.body).type for p in sent} == {MessageType.ACK}
    assert sim.outbox.empty()
    assert sim.publisher.dropped == 0


def test_stop_event_set_from_another_thread_ends_run_within_the_bound() -> None:
    sim = Simulator(
        CFG,
        registry=registry,
        consumer_connect=FakeBroker().connect,
        publisher_connect=FakeBroker().connect,
    )
    timer = threading.Timer(0.3, sim.stop)
    timer.start()
    done = threading.Event()
    result: list[int] = []

    def run() -> None:
        result.append(sim.run())
        done.set()

    threading.Thread(target=run, daemon=True).start()

    assert done.wait(10.0)
    assert result == [EXIT_OK]


def test_unreachable_broker_for_the_publisher_exits_non_zero() -> None:
    from skyhaul_drone_sim.amqp import BrokerUnavailable

    def refuse(timeout_s: float, stop: threading.Event | None) -> FakeConnection:
        raise BrokerUnavailable("no broker")

    sim = Simulator(CFG, registry=registry, consumer_connect=refuse, publisher_connect=refuse)

    assert sim.run() == EXIT_BROKER_UNAVAILABLE


def test_unreachable_broker_for_the_consumer_exits_non_zero() -> None:
    from skyhaul_drone_sim.amqp import BrokerUnavailable

    def refuse(timeout_s: float, stop: threading.Event | None) -> FakeConnection:
        raise BrokerUnavailable("no broker")

    sim = Simulator(
        CFG, registry=registry, consumer_connect=refuse, publisher_connect=FakeBroker().connect
    )

    assert sim.run() == EXIT_BROKER_UNAVAILABLE
    assert not sim.publisher.is_alive()
