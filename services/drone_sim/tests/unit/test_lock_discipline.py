"""Nothing blocks under the board lock, and handlers cannot reach an outbox or a channel."""

import queue
import threading
import time
import typing
from collections.abc import Callable
from typing import Any

import pytest
from sim_support.drones import board_with, make_drone
from sim_support.messages import alert_payload, command, outgoing, render_minimal
from skyhaul_contracts import CommandType, Message
from skyhaul_drone_sim.board import Job, JobsBoard, World
from skyhaul_drone_sim.model import Drone
from skyhaul_drone_sim.outbox import Outgoing
from skyhaul_drone_sim.registry import Handler, Registry

ALERT = alert_payload()


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


def tick(drone: Drone, job: Job, dt: float) -> list[Outgoing]:
    job.values["ticks"] = job.values.get("ticks", 0) + 1
    return [outgoing("ALERT", job.drone_id, payload=ALERT, persistent=True)]


def make_job(type: str) -> Job:
    return Job("main-d001", type, 1.0, 0.0, render=render_minimal, tick=tick)


def test_nothing_blocks_under_the_board_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    board = board_with()
    board.add_drone(make_drone("main-d001"))
    registry = Registry()

    @registry.handles(CommandType.TAKEOFF)
    def handle_takeoff(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
        world.upsert(make_job("POSITION"))
        return []

    @registry.handles(CommandType.GOTO)
    def handle_goto(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
        raise RuntimeError("bug")

    under_lock = spy_blocking_calls(monkeypatch, board)
    board.upsert(make_job("HEARTBEAT"))
    board.due(now=1.0, dt=0.1)
    for command_type in ("TAKEOFF", "GOTO", "LAND"):  # LAND has no handler
        with board.transaction() as world:
            registry.dispatch(world, command(command_type), now=1.0)
    with board.transaction() as world:  # an unknown drone
        registry.dispatch(world, command("ARM", drone_id="main-d999"), now=1.0)
    board.due(now=2.0, dt=0.1)
    board.snapshot()
    board.reset("other")
    board.is_current("main", 0)
    board.remove("main-d001", "HEARTBEAT")

    assert under_lock == []


def test_handlers_get_only_the_world_the_command_and_the_time() -> None:
    params, returns = typing.get_args(Handler.__value__)

    assert params == [World, Message[Any], float]
    assert returns == list[Outgoing]


def test_world_offers_only_in_memory_job_operations() -> None:
    public = {name for name in dir(World) if not name.startswith("_")}

    with board_with().transaction() as world:
        state = [getattr(world, slot) for slot in World.__slots__]

    assert public == {
        "get",
        "upsert",
        "remove",
        "discard",
        "has_drone",
        "drone",
        "add_drone",
        "count_unknown_drone_command",
        "stamp",
        "can_delay",
        "delay",
    }
    assert [type(value).__name__ for value in state] == [
        "_DelayedReplies",  # a heap and counters: in memory only
        "dict",
        "dict",
        "dict",
        "bool",
        "dict",
        "list",
        "dict",
        "int",
    ]
