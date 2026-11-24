"""The whole simulator in-process on fake connections, with its real control API on an
ephemeral port, run on a background thread named `consumer`."""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from skyhaul_drone_sim.config import SimConfig
from skyhaul_drone_sim.handlers import registry
from skyhaul_drone_sim.simulator import EXIT_OK, Simulator

from sim_support.config import sim_config
from sim_support.control_client import ControlClient
from sim_support.fakes import FakeBroker, FakeConnection

STOP_TIMEOUT_S = 10.0


@dataclass
class Running:
    sim: Simulator
    client: ControlClient
    consumers: FakeBroker
    publishers: FakeBroker
    thread: threading.Thread


@contextmanager
def running_simulator(cfg: SimConfig | None = None) -> Iterator[Running]:
    """Start the simulator, wait until `/healthz` is 200, yield, then stop it (exit 0)."""
    cfg = cfg if cfg is not None else sim_config()
    consumers, publishers = FakeBroker(), FakeBroker()
    sim = Simulator(
        cfg,
        registry=registry,
        consumer_connect=consumers.connect,
        publisher_connect=publishers.connect,
    )

    def prepare(connection: FakeConnection) -> None:
        # Stands in for the broker's poll wait, so the consumer does not spin.
        connection.on_poll = lambda: sim.stop_event.wait(0.01) and None

    consumers.prepare = prepare
    result: list[int] = []
    thread = threading.Thread(target=lambda: result.append(sim.run()), name="consumer", daemon=True)
    thread.start()
    try:
        client = ControlClient(_base_url(sim), cfg.control_token)
        client.wait_healthy()
        yield Running(sim, client, consumers, publishers, thread)
    finally:
        sim.stop()
        thread.join(STOP_TIMEOUT_S)
    assert not thread.is_alive(), "the simulator did not stop"
    assert result == [EXIT_OK]


def _base_url(sim: Simulator) -> str:
    """The control API's URL once it is bound (the port is picked by the OS)."""
    deadline = time.monotonic() + STOP_TIMEOUT_S
    while sim.control is None:
        assert time.monotonic() < deadline, "the control API never bound"
        time.sleep(0.01)
    return f"http://127.0.0.1:{sim.control.port}"
