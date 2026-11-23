"""Nothing blocks under the board lock, and handlers cannot reach an outbox or a channel."""

import queue
import threading
import time
import typing
from collections.abc import Callable
from typing import Any

import pytest
from skyhaul_drone_sim.board import Job, JobsBoard, World
from skyhaul_drone_sim.outbox import Outgoing
from skyhaul_drone_sim.registry import Command, Handler, Registry


def spy_blocking_calls(monkeypatch: pytest.MonkeyPatch, board: JobsBoard) -> list[str]:
    """Replace the blocking calls with spies that record any call made under the board lock."""
    under_lock: list[str] = []

    def spy(name: str) -> Callable[..., Any]:
        def record(*args: Any, **kwargs: Any) -> None:
            if board._lock.locked():
                under_lock.append(name)

        return record

    monkeypatch.setattr(time, "sleep", spy("time.sleep"))
    monkeypatch.setattr(threading.Event, "wait", spy("Event.wait"))
    monkeypatch.setattr(queue.Queue, "put", spy("Queue.put"))
    monkeypatch.setattr(queue.Queue, "put_nowait", spy("Queue.put_nowait"))
    return under_lock


def tick(job: Job, dt: float) -> list[Outgoing]:
    job.values["ticks"] = job.values.get("ticks", 0) + 1
    return [Outgoing(job.drone_id, "ALERT", None, {}, persistent=True)]


def make_job(type: str) -> Job:
    return Job("main-d001", type, 1.0, 0.0, render=lambda job: {}, tick=tick)


def test_nothing_blocks_under_the_board_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    board = JobsBoard()
    registry = Registry()

    @registry.handles("TAKEOFF")
    def handle_takeoff(world: World, command: Command, now: float) -> list[Outgoing]:
        world.upsert(make_job("POSITION"))
        return []

    @registry.handles("GOTO")
    def handle_goto(world: World, command: Command, now: float) -> list[Outgoing]:
        raise RuntimeError("bug")

    under_lock = spy_blocking_calls(monkeypatch, board)
    board.upsert(make_job("HEARTBEAT"))
    board.due(now=1.0, dt=0.1)
    for command_type in ("TAKEOFF", "GOTO", "UNKNOWN"):
        with board.transaction() as world:
            registry.dispatch(world, Command("m", None, "main-d001", command_type, {}), now=1.0)
    board.due(now=2.0, dt=0.1)
    board.snapshot()
    board.remove("main-d001", "HEARTBEAT")

    assert under_lock == []


def test_handlers_get_only_the_world_the_command_and_the_time() -> None:
    params, returns = typing.get_args(Handler.__value__)

    assert params == [World, Command, float]
    assert returns == list[Outgoing]


def test_world_offers_only_in_memory_job_operations() -> None:
    public = {name for name in dir(World) if not name.startswith("_")}

    with JobsBoard().transaction() as world:
        state = [getattr(world, slot) for slot in World.__slots__]

    assert public == {"get", "upsert", "remove"}
    assert [type(value) for value in state] == [dict, bool]
