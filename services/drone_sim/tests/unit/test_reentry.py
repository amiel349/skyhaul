"""Taking the board lock again from the thread that holds it raises instead of hanging."""

import threading
from collections.abc import Callable
from typing import Any

from sim_support.messages import command, payload_of, render_minimal
from skyhaul_contracts import CommandType, Message
from skyhaul_drone_sim.board import DueResult, Job, JobsBoard, World
from skyhaul_drone_sim.outbox import Outgoing
from skyhaul_drone_sim.registry import DispatchResult, Registry

TIMEOUT_S = 5.0


def finishes[T](work: Callable[[], T]) -> T:
    """Run `work` on a daemon thread and fail the test, instead of hanging, if it never returns."""
    results: list[T] = []
    thread = threading.Thread(target=lambda: results.append(work()), daemon=True)
    thread.start()
    thread.join(TIMEOUT_S)
    assert not thread.is_alive(), "the call hung: the board lock was taken twice"
    return results[0]


def test_tick_that_calls_the_board_gets_a_job_error_instead_of_hanging() -> None:
    board = JobsBoard()

    def snapshot_tick(job: Job, dt: float) -> list[Outgoing]:
        board.snapshot()
        return []

    board.upsert(
        Job("main-d001", "HEARTBEAT", 1.0, 100.0, render=render_minimal, tick=snapshot_tick)
    )

    result: DueResult = finishes(lambda: board.due(now=1.0, dt=0.1))

    (error,) = result.errors
    assert isinstance(error.error, RuntimeError)
    assert "taken again by the thread that holds it" in str(error.error)


def test_handler_that_calls_the_board_gets_a_nack_instead_of_hanging() -> None:
    board = JobsBoard()
    registry = Registry()

    board.add_drone("main-d001")

    @registry.handles(CommandType.ARM)
    def handle_arm(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
        board.upsert(Job("main-d001", "POSITION", 1.0, 1.0, render=render_minimal))
        return []

    def dispatch() -> DispatchResult:
        with board.transaction() as world:
            return registry.dispatch(world, command("ARM"), now=0.0)

    result = finishes(dispatch)

    (reply,) = result.replies
    assert payload_of(reply)["reason"] == "INTERNAL_ERROR"
    assert isinstance(result.error, RuntimeError)
    assert board.snapshot() == []


def test_board_is_usable_from_any_thread_after_a_refused_reentry() -> None:
    board = JobsBoard()

    def snapshot_tick(job: Job, dt: float) -> list[Outgoing]:
        board.snapshot()
        return []

    board.upsert(
        Job("main-d001", "HEARTBEAT", 1.0, 100.0, render=render_minimal, tick=snapshot_tick)
    )
    finishes(lambda: board.due(now=1.0, dt=0.1))

    assert finishes(board.snapshot)[0]["type"] == "HEARTBEAT"
    assert len(board.snapshot()) == 1
