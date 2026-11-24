"""Jobs board invariants under many threads. Threads start together on a barrier."""

import dataclasses
import random
import threading
from collections.abc import Callable
from typing import Any

from sim_support.messages import command as make_command
from sim_support.messages import render_minimal
from skyhaul_contracts import CommandType, Message
from skyhaul_drone_sim.board import Job, JobError, JobsBoard, World
from skyhaul_drone_sim.outbox import Outgoing
from skyhaul_drone_sim.registry import Registry

JOIN_TIMEOUT_S = 30.0


def run_together(workers: list[Callable[[], None]]) -> None:
    """Start every worker at the same moment, wait for all, and re-raise the first error."""
    barrier = threading.Barrier(len(workers))
    errors: list[BaseException] = []

    def run(work: Callable[[], None]) -> None:
        try:
            barrier.wait(timeout=JOIN_TIMEOUT_S)
            work()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(work,)) for work in workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=JOIN_TIMEOUT_S)

    assert not [thread for thread in threads if thread.is_alive()], "a worker did not finish"
    if errors:
        raise errors[0]


def make_job(drone_id: str, type: str, next_due: float = 1.0, **values: int) -> Job:
    return Job(drone_id, type, 1.0, next_due, render=render_minimal, values=dict(values))


def job_keys(board: JobsBoard) -> list[tuple[str, str]]:
    return [(job["drone_id"], job["type"]) for job in board.snapshot()]


def test_one_job_per_drone_and_type_when_32_threads_upsert_at_once() -> None:
    """Every thread upserts every key with its own period; the earliest next_due must win.

    Each upsert reads the stored job and writes a new one, so without the lock an
    upsert can be lost and a later next_due can survive.
    """
    board = JobsBoard()
    drones = [f"main-d{n:03}" for n in range(100)]

    def upsert_all(index: int) -> Callable[[], None]:
        def work() -> None:
            for drone_id in drones:
                period_s = float(index + 1)
                board.upsert(Job(drone_id, "HEARTBEAT", period_s, period_s, render=render_minimal))

        return work

    run_together([upsert_all(index) for index in range(32)])

    jobs = board.snapshot()
    assert sorted(job_keys(board)) == [(drone_id, "HEARTBEAT") for drone_id in drones]
    assert {job["drone_id"]: job["next_due"] for job in jobs} == dict.fromkeys(drones, 1.0)


def test_one_job_per_drone_and_type_when_32_threads_create_it_if_missing() -> None:
    """Check-then-act inside one transaction: exactly one thread creates each job."""
    board = JobsBoard()
    drones = [f"main-d{n:03}" for n in range(100)]
    creators: dict[str, list[int]] = {drone_id: [] for drone_id in drones}

    def create_if_missing(index: int) -> Callable[[], None]:
        def work() -> None:
            for drone_id in drones:
                with board.transaction() as world:
                    if world.get(drone_id, "POSITION") is None:
                        world.upsert(make_job(drone_id, "POSITION"))
                        creators[drone_id].append(index)

        return work

    run_together([create_if_missing(index) for index in range(32)])

    assert {drone_id: len(found) for drone_id, found in creators.items()} == dict.fromkeys(
        drones, 1
    )
    assert sorted(job_keys(board)) == [(drone_id, "POSITION") for drone_id in drones]


def test_at_most_one_job_per_drone_and_type_after_16_threads_of_1000_upserts_and_removes() -> None:
    """16 threads x 1,000 random upserts and removes while the scheduler ticks the board.

    Without the lock on upsert or remove, `due()` fails with "dictionary changed
    size during iteration".
    """
    board = JobsBoard()
    drones = [f"main-d{n:03}" for n in range(50)]
    types = ["HEARTBEAT", "BATTERY", "POSITION"]
    churn_done = threading.Event()
    due_calls = 0
    job_errors: list[JobError] = []

    def churn(seed: int) -> Callable[[], None]:
        def work() -> None:
            rng = random.Random(seed)  # noqa: S311 - seeded test data, not security
            for _ in range(1000):
                drone_id, type = rng.choice(drones), rng.choice(types)
                if rng.random() < 0.6:
                    board.upsert(make_job(drone_id, type))
                else:
                    board.remove(drone_id, type)

        return work

    def all_churners() -> None:
        try:
            run_together([churn(seed) for seed in range(16)])
        finally:
            churn_done.set()

    def scheduler() -> None:
        nonlocal due_calls
        now = 0.0
        while not churn_done.is_set():
            now += 0.1
            job_errors.extend(board.due(now, 0.1).errors)
            due_calls += 1

    run_together([all_churners, scheduler])

    keys = job_keys(board)
    assert due_calls > 0
    assert job_errors == []
    assert len(keys) == len(set(keys))


def test_tick_and_command_upsert_on_one_job_10000_times_each_lose_no_update() -> None:
    """The tick changes field A, the command replaces the whole job with field B changed."""
    runs = 10_000
    board = JobsBoard()
    board.add_drone("main-d001")
    registry = Registry()

    def count_tick(job: Job, dt: float) -> list[Outgoing]:
        job.values["a"] += 1
        return []

    @registry.handles(CommandType.SET_SPEED)
    def handle_set_speed(world: World, command: Message[Any], now: float) -> list[Outgoing]:
        job = world.get(command.drone_id, "POSITION")
        assert job is not None
        world.upsert(dataclasses.replace(job, values={**job.values, "b": job.values["b"] + 1}))
        return []

    board.upsert(
        Job(
            "main-d001",
            "POSITION",
            1.0,
            1e9,
            render=render_minimal,
            tick=count_tick,
            values={"a": 0, "b": 0},
        )
    )
    command = make_command("SET_SPEED")

    def ticks() -> None:
        for n in range(runs):
            board.due(now=n * 0.1, dt=0.1)

    def commands() -> None:
        for n in range(runs):
            with board.transaction() as world:
                registry.dispatch(world, command, now=n * 0.1)

    run_together([ticks, commands])

    (job,) = board.snapshot()
    assert job["values"] == {"a": runs, "b": runs}
