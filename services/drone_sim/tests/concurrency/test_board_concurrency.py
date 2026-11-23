"""Jobs board invariants under many threads. Threads start together on a barrier."""

import dataclasses
import random
import threading
from collections.abc import Callable

from skyhaul_drone_sim.board import Job, JobsBoard, World
from skyhaul_drone_sim.outbox import Outgoing
from skyhaul_drone_sim.registry import Command, Registry

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
    return Job(drone_id, type, 1.0, next_due, render=lambda job: {}, values=dict(values))


def job_keys(board: JobsBoard) -> list[tuple[str, str]]:
    return [(job["drone_id"], job["type"]) for job in board.snapshot()]


def test_one_job_per_drone_and_type_when_32_threads_upsert_at_once() -> None:
    board = JobsBoard()

    def upsert(index: int) -> Callable[[], None]:
        return lambda: board.upsert(
            make_job("main-d001", "HEARTBEAT", next_due=index, writer=index)
        )

    run_together([upsert(index) for index in range(32)])

    (job,) = board.snapshot()
    assert (job["drone_id"], job["type"]) == ("main-d001", "HEARTBEAT")
    assert job["next_due"] in range(32)
    assert job["values"]["writer"] in range(32)


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
    board = JobsBoard()
    drones = [f"main-d00{n}" for n in range(1, 5)]
    types = ["HEARTBEAT", "BATTERY", "POSITION"]

    def churn(seed: int) -> Callable[[], None]:
        def work() -> None:
            rng = random.Random(seed)  # noqa: S311 - seeded test data, not security
            for _ in range(1000):
                drone_id, type = rng.choice(drones), rng.choice(types)
                if rng.random() < 0.6:
                    board.upsert(make_job(drone_id, type, writer=seed))
                else:
                    board.remove(drone_id, type)

        return work

    run_together([churn(seed) for seed in range(16)])

    keys = job_keys(board)
    assert len(keys) == len(set(keys))
    assert set(keys) <= {(drone_id, type) for drone_id in drones for type in types}


def test_tick_and_command_upsert_on_one_job_10000_times_each_lose_no_update() -> None:
    """The tick changes field A, the command replaces the whole job with field B changed."""
    runs = 10_000
    board = JobsBoard()
    registry = Registry()

    def count_tick(job: Job, dt: float) -> list[Outgoing]:
        job.values = {**job.values, "a": job.values["a"] + 1}
        return []

    @registry.handles("SET_SPEED")
    def handle_set_speed(world: World, command: Command, now: float) -> list[Outgoing]:
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
            render=lambda job: {},
            tick=count_tick,
            values={"a": 0, "b": 0},
        )
    )
    command = Command("msg-1", "corr-1", "main-d001", "SET_SPEED", {})

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
