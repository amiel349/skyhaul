"""The shutdown bound with every thread busy: a full outbox and a publisher the broker never
confirms. The stop event still ends the run, with exit 0, inside 10 s, and the rest is
reported as dropped."""

import logging
import threading
import time

import pytest
from sim_support.config import sim_config
from sim_support.fakes import FakeBroker, FakeConnection
from sim_support.messages import DRONE, command
from skyhaul_contracts import Codec
from skyhaul_drone_sim.handlers import registry
from skyhaul_drone_sim.simulator import EXIT_OK, Simulator

SHUTDOWN_BOUND_S = 10.0


def test_stop_with_a_full_outbox_and_a_stuck_publisher_exits_0_within_10s(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cfg = sim_config(outbox_max=10, drain_s=1.0, drones=(DRONE,))
    consumers, publishers = FakeBroker(), FakeBroker()
    never = threading.Event()
    publishers.prepare = lambda c: setattr(c, "publish_gate", never)
    sim = Simulator(
        cfg,
        registry=registry,
        consumer_connect=consumers.connect,
        publisher_connect=publishers.connect,
    )
    body = Codec().encode(command("ARM"))

    def prepare(connection: FakeConnection) -> None:
        for _ in range(50):
            connection.deliver(f"drone.{DRONE}.ARM", body)

    consumers.prepare = prepare
    timer = threading.Timer(0.5, sim.stop)  # the consumer is blocked on the full outbox
    result: list[int] = []
    done = threading.Event()

    def run() -> None:
        result.append(sim.run())
        done.set()

    started = time.monotonic()
    timer.start()
    with caplog.at_level(logging.INFO):
        threading.Thread(target=run, daemon=True).start()
        assert done.wait(SHUTDOWN_BOUND_S)
    elapsed = time.monotonic() - started
    never.set()  # release the stuck daemon publisher

    assert result == [EXIT_OK]
    assert elapsed < SHUTDOWN_BOUND_S
    (connection,) = consumers.connections
    acks = connection.channels[0].acks
    assert len(acks) < 50  # the rest stay unacked: the broker requeues them
    assert connection.channels[0].closed
    (stopped,) = [r for r in caplog.records if r.message == "simulator stopped"]
    assert stopped.dropped >= 10  # type: ignore[attr-defined]
