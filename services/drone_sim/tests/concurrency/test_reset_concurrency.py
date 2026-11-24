"""Reset against ticks and commands on the same namespace, and reset epochs against messages
still waiting in the outbox (design note invariant 14, ADR 0009)."""

import threading
import time

from sim_support.config import sim_config
from sim_support.fakes import FakeBroker
from sim_support.messages import command
from skyhaul_contracts import Codec
from skyhaul_drone_sim.behaviors import spawn_drone
from skyhaul_drone_sim.board import Job, JobsBoard, MakeDrone
from skyhaul_drone_sim.handlers import registry
from skyhaul_drone_sim.model import Drone, GeoPoint
from skyhaul_drone_sim.outbox import SENTINEL
from skyhaul_drone_sim.simulator import Simulator

HOME = GeoPoint(32.0, 34.8)
ROUNDS = 20


def make(now: float) -> MakeDrone:
    def build(drone_id: str) -> tuple[Drone, list[Job]]:
        return spawn_drone(drone_id, home=HOME, now=now, seed=1, geofence_radius_m=5000.0)

    return build


def w1_state(board: JobsBoard) -> tuple[int, int]:
    jobs = [job for job in board.snapshot() if job["drone_id"].startswith("w1-")]
    return len(board.drones("w1")), len(jobs)


def w2_keys(board: JobsBoard) -> list[tuple[str, str]]:
    return sorted(
        (j["drone_id"], j["type"]) for j in board.snapshot() if j["drone_id"].startswith("w2-")
    )


def keep_ticking(board: JobsBoard, stop: threading.Event) -> None:
    now = 0.0
    while not stop.is_set():
        now += 0.1
        board.due(now, 0.1)


def keep_commanding_w1(board: JobsBoard, stop: threading.Event) -> None:
    n = 0
    while not stop.is_set():
        n += 1
        msg = command("ARM" if n % 2 else "SET_SPEED", drone_id=f"w1-d{n % 10 + 1:03}")
        with board.transaction() as world:
            registry.dispatch(world, msg, 0.0)


def test_reset_during_ticks_and_commands_leaves_no_w1_drone_or_job_and_w2_untouched() -> None:
    for _ in range(ROUNDS):
        board = JobsBoard()
        board.spawn("w1", 10, make(0.0))
        board.spawn("w2", 10, make(0.0))
        w2_drones, w2_jobs = [d.drone_id for d in board.drones("w2")], w2_keys(board)
        stop = threading.Event()
        workers = [
            threading.Thread(target=keep_ticking, args=(board, stop)),
            threading.Thread(target=keep_commanding_w1, args=(board, stop)),
        ]
        for worker in workers:
            worker.start()
        time.sleep(0.01)
        result = board.reset("w1")
        after = [w1_state(board) for _ in range(20)]  # ticks and commands still running
        stop.set()
        for worker in workers:
            worker.join(5)

        assert (result.drones_removed, result.epoch) == (10, 1)
        assert set(after) == {(0, 0)}
        assert w1_state(board) == (0, 0)
        assert [d.drone_id for d in board.drones("w2")] == w2_drones
        assert w2_keys(board) == w2_jobs


def test_messages_queued_before_a_reset_are_never_published_after_it_returns() -> None:
    publishers = FakeBroker()
    gate = threading.Event()
    publishers.prepare = lambda connection: setattr(connection, "publish_gate", gate)
    sim = Simulator(
        sim_config(outbox_max=1000),
        registry=registry,
        consumer_connect=FakeBroker().connect,
        publisher_connect=publishers.connect,
    )
    sim.board.spawn("w1", 5, make(0.0))
    sim.board.spawn("w2", 5, make(0.0))
    queued = []
    for second in range(1, 11):  # ten seconds of telemetry for both namespaces, unpublished
        queued += sim.board.due(float(second), 1.0).messages
    for out in queued:
        sim.outbox.put(out)
    sim.publisher.start()
    assert sim.publisher.ready.wait(5)

    resetting = threading.Thread(target=lambda: sim.reset("w1"))
    resetting.start()  # the publisher is stuck on its first message, checked before the reset
    time.sleep(0.05)
    gate.set()
    resetting.join(5)
    (connection,) = publishers.connections
    published_by_reset = [p.routing_key for p in connection.channels[0].published]
    sim.outbox.put(SENTINEL)
    sim.stop()
    sim.publisher.join(5)

    sent = [p.routing_key for p in connection.channels[0].published]
    w1_queued = sum(out.namespace == "w1" for out in queued)
    w1_sent = [key for key in sent if key.startswith("drone.w1-")]
    assert [key for key in published_by_reset if key.startswith("drone.w1-")] == w1_sent
    assert len(w1_sent) <= 1  # at most the one in flight, and it went out before the return
    assert sum(key.startswith("drone.w2-") for key in sent) == len(queued) - w1_queued
    assert sim.publisher.stats().dropped_stale == w1_queued - len(w1_sent)
    assert all(Codec().decode(p.body) for p in connection.channels[0].published)
