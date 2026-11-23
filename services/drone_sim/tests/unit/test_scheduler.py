"""The scheduler loop itself, driven by a FakeClock: drift-free, no catch-up burst, and a
failing tick or job never stops it."""

import logging
import threading

import pytest
from sim_support.messages import render_minimal
from skyhaul_drone_sim.board import Job, JobsBoard
from skyhaul_drone_sim.clock import FakeClock
from skyhaul_drone_sim.outbox import Outbox, Outgoing, new_outbox
from skyhaul_drone_sim.scheduler import Scheduler


def fake_wait(clock: FakeClock, until: float, jumps: dict[int, float] | None = None):  # type: ignore[no-untyped-def]
    """A `wait` that advances the clock instead of sleeping. Call number `n` in `jumps`
    advances by that many seconds instead (a stall). Stops once the clock passes `until`."""
    calls = 0

    def wait(timeout_s: float) -> bool:
        nonlocal calls
        calls += 1
        step = (jumps or {}).get(calls, timeout_s)
        if clock.monotonic() + step > until:
            return True
        clock.advance(step)
        return False

    return wait


def run_scheduler(
    board: JobsBoard, clock: FakeClock, until: float, jumps: dict[int, float] | None = None
) -> Outbox:
    outbox = new_outbox(10_000)
    scheduler = Scheduler(
        board=board,
        outbox=outbox,
        clock=clock,
        stop=threading.Event(),
        tick_s=0.1,
        wait=fake_wait(clock, until, jumps),
    )
    scheduler.run()
    return outbox


def drain(outbox: Outbox) -> list[Outgoing]:
    items = [outbox.get_nowait() for _ in range(outbox.qsize())]
    return [item for item in items if isinstance(item, Outgoing)]


def test_scheduler_loop_emits_a_1s_job_exactly_10_times_in_10s() -> None:
    clock = FakeClock()
    board = JobsBoard()
    board.upsert(Job("main-d001", "HEARTBEAT", 1.0, 1.0, render=render_minimal))

    emitted = drain(run_scheduler(board, clock, until=10.0))

    assert clock.monotonic() == 10.0
    assert [out.message.type for out in emitted] == ["HEARTBEAT"] * 10


def test_scheduler_loop_after_a_10s_clock_jump_emits_a_1s_job_once_not_10_times() -> None:
    clock = FakeClock()
    board = JobsBoard()
    board.upsert(Job("main-d001", "HEARTBEAT", 1.0, 0.5, render=render_minimal))

    # Call 1 waits 0 s (the first tick), call 2 is the stall.
    emitted = drain(run_scheduler(board, clock, until=10.05, jumps={2: 10.0}))

    assert clock.monotonic() == 10.0
    assert len(emitted) == 1
    assert board.snapshot()[0]["next_due"] == 10.5  # still on its own grid


def test_scheduler_tick_with_an_empty_board_puts_nothing() -> None:
    assert drain(run_scheduler(JobsBoard(), FakeClock(), until=2.0)) == []


def test_raising_job_is_logged_outside_the_lock_and_other_jobs_keep_emitting(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    board = JobsBoard()

    def broken(job: Job) -> object:
        raise ZeroDivisionError("render bug")

    board.upsert(Job("main-d001", "BATTERY", 1.0, 1.0, render=broken))  # type: ignore[arg-type]
    board.upsert(Job("main-d002", "HEARTBEAT", 1.0, 1.0, render=render_minimal))
    locked_while_logging: list[bool] = []

    class Spy(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            locked_while_logging.append(board._lock.locked())

    spy = Spy()
    logging.getLogger("skyhaul_drone_sim.scheduler").addHandler(spy)
    try:
        with caplog.at_level(logging.ERROR):
            emitted = drain(run_scheduler(board, clock, until=3.05))
    finally:
        logging.getLogger("skyhaul_drone_sim.scheduler").removeHandler(spy)

    assert [out.message.drone_id for out in emitted] == ["main-d002"] * 3
    failures = [r for r in caplog.records if r.message == "job failed"]
    assert len(failures) == 3
    assert all(r.exc_info is not None for r in failures)
    assert locked_while_logging
    assert not any(locked_while_logging)


def test_a_tick_that_raises_does_not_stop_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    board = JobsBoard()
    board.upsert(Job("main-d001", "HEARTBEAT", 1.0, 1.0, render=render_minimal))
    real_due = board.due
    calls = 0

    def flaky_due(now: float, dt: float):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 5:
            raise RuntimeError("clock broke once")
        return real_due(now, dt)

    monkeypatch.setattr(board, "due", flaky_due)

    emitted = drain(run_scheduler(board, clock, until=3.05))

    assert len(emitted) == 3
    assert calls == 31


def test_scheduler_thread_stops_promptly_on_the_stop_event() -> None:
    stop = threading.Event()
    scheduler = Scheduler(
        board=JobsBoard(), outbox=new_outbox(1), clock=FakeClock(), stop=stop, tick_s=0.1
    )
    thread = threading.Thread(target=scheduler.run)
    thread.start()

    stop.set()
    thread.join(2.0)

    assert not thread.is_alive()
