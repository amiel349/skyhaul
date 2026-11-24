"""A new GOTO against the movement tick, and exactly four threads however many drones."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from itertools import pairwise

import pytest
from sim_support.drones import spawned_board
from sim_support.flight import goto, offset_point
from sim_support.messages import DRONE, command
from sim_support.running import running_simulator
from skyhaul_contracts import FlightState, MessageType
from skyhaul_contracts.v1 import TakeoffPayload
from skyhaul_drone_sim import behaviors
from skyhaul_drone_sim.board import Job, JobsBoard
from skyhaul_drone_sim.handlers import registry
from skyhaul_drone_sim.model import Drone, GeoPoint
from skyhaul_drone_sim.outbox import Outgoing

NORTH = offset_point(north_m=2000.0)  # target A
SOUTH = offset_point(north_m=-2000.0)  # target B
ROUNDS = 40


@dataclass(frozen=True)
class Step:
    target: GeoPoint | None
    lat_before: float
    lat_after: float


def send(board: JobsBoard, msg_type: str, payload: object = None, now: float = 0.0) -> None:
    msg = command(msg_type, payload=payload)  # type: ignore[arg-type]
    with board.transaction() as world:
        result = registry.dispatch(world, msg, now)
    assert [r.message.type for r in result.replies][:1] == [MessageType.ACK]


def test_goto_b_while_moving_to_a_means_no_later_tick_moves_toward_a(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steps: list[Step] = []
    real_tick = behaviors.flight_tick

    def recording_tick(d: Drone, job: Job, dt: float) -> list[Outgoing]:
        # Runs under the board lock, so the list is in tick order.
        target = None if d.target is None else d.target.point
        before = d.pos.lat
        events = real_tick(d, job, dt)
        steps.append(Step(target, before, d.pos.lat))
        return events

    monkeypatch.setattr(behaviors, "flight_tick", recording_tick)
    board = spawned_board(DRONE)
    send(board, "ARM")
    send(board, "TAKEOFF", TakeoffPayload(altitude_m=30.0))
    stop = threading.Event()

    def ticks() -> None:
        while not stop.is_set():
            board.due(0.0, 0.01)  # 0.1 m per tick: never arrives during the test

    ticker = threading.Thread(target=ticks)
    ticker.start()
    try:
        while True:  # climb to hovering on the ticker's thread
            drone = board.drone(DRONE)
            assert drone is not None
            if drone.state is FlightState.HOVERING:
                break
        marks = []
        for _ in range(ROUNDS):
            send(board, "GOTO", goto(NORTH))
            wait_for_steps(steps, len(steps) + 5)  # some ticks toward A
            send(board, "GOTO", goto(SOUTH))
            marks.append(len(steps))  # every step from here on is after GOTO B
            wait_for_steps(steps, len(steps) + 5)
    finally:
        stop.set()
        ticker.join()

    for mark in marks:
        for step in steps[mark:]:
            if step.target == NORTH:  # the next round's GOTO A
                break
            assert step.target == SOUTH
            assert step.lat_after < step.lat_before  # toward B, never toward A
    # No tick or command overwrote another's change: each step starts where the last ended.
    for previous, step in pairwise(steps):
        assert step.lat_before == previous.lat_after


def wait_for_steps(steps: list[Step], count: int, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while len(steps) < count:
        assert time.monotonic() < deadline, "the ticker stopped"


def count_threads_while_running(drone_count: int) -> tuple[int, set[str]]:
    """Spawn `drone_count` drones through the control API, wait until each has sent
    telemetry, then count the threads."""
    with running_simulator() as running:
        left = drone_count
        while left:
            running.client.spawn("main", min(left, 100))
            left -= min(left, 100)
        deadline = time.monotonic() + 10.0
        while running.sim.publisher.stats().published < drone_count:
            assert time.monotonic() < deadline, "the drones never sent telemetry"
            time.sleep(0.01)
        assert running.sim.board.drone_count() == drone_count
        threads = threading.enumerate()
        return len(threads), {t.name for t in threads}


def test_running_simulator_has_exactly_four_threads_with_1_and_with_200_drones() -> None:
    before = threading.enumerate()

    one = count_threads_while_running(1)
    many = count_threads_while_running(200)

    assert one == many
    count, names = many
    assert count == len(before) + 4  # consumer, scheduler, publisher, control-http
    assert names == {t.name for t in before} | {
        "consumer",
        "scheduler",
        "publisher",
        "control-http",
    }
