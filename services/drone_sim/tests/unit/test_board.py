import math
from typing import Any

import pytest
from skyhaul_drone_sim.board import MAX_TICK_DT_S, DueResult, Job, JobError, JobsBoard, TickFn
from skyhaul_drone_sim.clock import FakeClock
from skyhaul_drone_sim.outbox import Outgoing


def render_values(job: Job) -> object:
    return dict(job.values)


def make_job(
    drone_id: str = "main-d001",
    type: str = "HEARTBEAT",
    period_s: float = 1.0,
    next_due: float = 1.0,
    tick: TickFn | None = None,
    correlation_id: str | None = None,
    values: dict[str, Any] | None = None,
) -> Job:
    return Job(
        drone_id=drone_id,
        type=type,
        period_s=period_s,
        next_due=next_due,
        render=render_values,
        tick=tick,
        correlation_id=correlation_id,
        values=values if values is not None else {},
    )


def only_job(board: JobsBoard) -> dict[str, Any]:
    (job,) = board.snapshot()
    return job


# --- upsert, remove, transaction ---


def test_upsert_replaces_job_with_same_key_and_keeps_its_next_due() -> None:
    board = JobsBoard()
    board.upsert(make_job(next_due=5.0, values={"v": 1}))

    board.upsert(make_job(next_due=99.0, values={"v": 2}))

    job = only_job(board)
    assert job["values"] == {"v": 2}
    assert job["next_due"] == 5.0


def test_upsert_keeps_jobs_with_different_keys_apart() -> None:
    board = JobsBoard()

    board.upsert(make_job(drone_id="main-d001", type="HEARTBEAT"))
    board.upsert(make_job(drone_id="main-d001", type="BATTERY"))
    board.upsert(make_job(drone_id="main-d002", type="HEARTBEAT"))

    keys = {(job["drone_id"], job["type"]) for job in board.snapshot()}
    assert keys == {
        ("main-d001", "HEARTBEAT"),
        ("main-d001", "BATTERY"),
        ("main-d002", "HEARTBEAT"),
    }


NOT_FINITE_OR_POSITIVE = [0.0, -1.0, math.nan, math.inf, -math.inf]


@pytest.mark.parametrize("period_s", NOT_FINITE_OR_POSITIVE)
def test_board_upsert_rejects_a_period_that_is_not_finite_and_positive(period_s: float) -> None:
    board = JobsBoard()

    with pytest.raises(ValueError, match="period_s must be finite and positive"):
        board.upsert(make_job(period_s=period_s))

    assert board.snapshot() == []


@pytest.mark.parametrize("period_s", NOT_FINITE_OR_POSITIVE)
def test_world_upsert_rejects_a_period_that_is_not_finite_and_positive(period_s: float) -> None:
    board = JobsBoard()

    with board.transaction() as world, pytest.raises(ValueError, match="period_s must be finite"):
        world.upsert(make_job(period_s=period_s))

    assert board.snapshot() == []


@pytest.mark.parametrize("next_due", [math.nan, math.inf, -math.inf])
def test_upsert_rejects_a_next_due_that_is_not_finite(next_due: float) -> None:
    board = JobsBoard()

    with pytest.raises(ValueError, match="next_due must be finite"):
        board.upsert(make_job(next_due=next_due))

    assert board.snapshot() == []


def test_remove_reports_whether_a_job_was_removed() -> None:
    board = JobsBoard()
    board.upsert(make_job())

    assert board.remove("main-d001", "HEARTBEAT") is True
    assert board.remove("main-d001", "HEARTBEAT") is False
    assert board.snapshot() == []


def test_transaction_changes_are_on_the_board_after_the_block() -> None:
    board = JobsBoard()
    board.upsert(make_job(values={"speed": 10}))

    with board.transaction() as world:
        job = world.get("main-d001", "HEARTBEAT")
        assert job is not None
        job.values["speed"] = 12
        world.upsert(make_job(type="BATTERY"))
        world.remove("main-d001", "MISSING")

    types = {job["type"]: job["values"] for job in board.snapshot()}
    assert types == {"HEARTBEAT": {"speed": 12}, "BATTERY": {}}


def test_world_cannot_be_used_after_its_transaction_ends() -> None:
    board = JobsBoard()
    with board.transaction() as world:
        pass

    with pytest.raises(RuntimeError, match=r"outside its JobsBoard\.transaction"):
        world.get("main-d001", "HEARTBEAT")
    with pytest.raises(RuntimeError, match=r"outside its JobsBoard\.transaction"):
        world.upsert(make_job())
    with pytest.raises(RuntimeError, match=r"outside its JobsBoard\.transaction"):
        world.remove("main-d001", "HEARTBEAT")


def test_transaction_releases_the_lock_when_the_block_raises() -> None:
    board = JobsBoard()

    with pytest.raises(KeyError), board.transaction():
        raise KeyError("boom")

    board.upsert(make_job())  # would deadlock if the lock were still held
    assert len(board.snapshot()) == 1


# --- reads return copies ---


def test_reads_return_copies_changing_a_snapshot_does_not_change_the_board() -> None:
    board = JobsBoard()
    board.upsert(make_job(next_due=3.0, values={"pos": {"lat": 1.0}}))

    job = only_job(board)
    job["next_due"] = 999.0
    job["values"]["pos"]["lat"] = 50.0
    job["values"]["extra"] = True

    assert only_job(board) == {
        "drone_id": "main-d001",
        "type": "HEARTBEAT",
        "period_s": 1.0,
        "next_due": 3.0,
        "correlation_id": None,
        "values": {"pos": {"lat": 1.0}},
    }


def test_reads_return_copies_changing_an_upserted_job_does_not_change_the_board() -> None:
    board = JobsBoard()
    job = make_job(values={"pos": {"lat": 1.0}})
    board.upsert(job)

    job.values["pos"]["lat"] = 50.0
    job.next_due = 999.0

    stored = only_job(board)
    assert stored["values"] == {"pos": {"lat": 1.0}}
    assert stored["next_due"] == 1.0


# --- due: periods, clock jumps, ticks ---


def test_due_on_an_empty_board_returns_nothing() -> None:
    assert JobsBoard().due(now=10.0, dt=0.1) == DueResult([], [])


def test_job_with_1s_period_is_due_exactly_10_times_over_10s_on_a_fake_clock() -> None:
    clock = FakeClock()
    board = JobsBoard()
    board.upsert(make_job(period_s=1.0, next_due=clock.monotonic() + 1.0))

    emitted: list[Outgoing] = []
    for _ in range(100):  # 100 scheduler ticks of 100 ms
        clock.advance(0.1)
        emitted += board.due(clock.monotonic(), 0.1).messages

    assert clock.monotonic() == 10.0
    assert len(emitted) == 10


def test_clock_jump_of_10s_emits_a_1s_job_once_not_10_times() -> None:
    clock = FakeClock()
    board = JobsBoard()
    board.upsert(make_job(period_s=1.0, next_due=clock.monotonic() + 1.0))

    clock.advance(10.0)
    after_jump = board.due(clock.monotonic(), 10.0).messages
    clock.advance(0.1)
    next_tick = board.due(clock.monotonic(), 0.1).messages

    assert len(after_jump) == 1
    assert next_tick == []
    assert only_job(board)["next_due"] == 11.0


def test_due_emits_when_now_reaches_next_due_then_schedules_one_period_on_its_grid() -> None:
    board = JobsBoard()
    board.upsert(make_job(period_s=2.0, next_due=5.0))

    before = board.due(now=4.9, dt=0.1).messages
    at = board.due(now=5.3, dt=0.1).messages

    assert before == []
    assert len(at) == 1
    assert only_job(board)["next_due"] == 7.0


def test_1s_job_does_not_drift_when_ticks_wake_late_by_0_to_90_ms_over_100s() -> None:
    clock = FakeClock()
    board = JobsBoard()
    board.upsert(make_job(period_s=1.0, next_due=1.0))

    now_ms = 0
    emitted_at: list[float] = []
    scheduled: list[float] = []
    for tick in range(1, 1001):  # 1,000 ticks of 100 ms
        late_ms = (tick * 37) % 91  # varies over 0..90 ms
        wake_ms = tick * 100 + late_ms
        clock.advance((wake_ms - now_ms) / 1000)
        now_ms = wake_ms
        due_at = only_job(board)["next_due"]
        if board.due(clock.monotonic(), 0.1).messages:
            emitted_at.append(clock.monotonic())
            scheduled.append(due_at)

    assert len(emitted_at) == 100
    assert scheduled == [float(n) for n in range(1, 101)]
    assert all(0.0 <= at - due < 0.1 for at, due in zip(emitted_at, scheduled, strict=True))
    assert only_job(board)["next_due"] == 101.0


def test_emission_carries_the_job_payload_and_correlation_id_and_is_transient() -> None:
    board = JobsBoard()
    board.upsert(make_job(correlation_id="corr-1", values={"armed": False}))

    (out,) = board.due(now=1.0, dt=0.1).messages

    assert out == Outgoing(
        drone_id="main-d001",
        type="HEARTBEAT",
        correlation_id="corr-1",
        payload={"armed": False},
        persistent=False,
    )


def test_tick_runs_on_every_call_and_its_events_are_returned_even_when_not_due() -> None:
    event = Outgoing("main-d001", "ALERT", None, {"code": "LOW_BATTERY"}, persistent=True)

    def tick(job: Job, dt: float) -> list[Outgoing]:
        job.values["ticks"] += 1
        return [event]

    board = JobsBoard()
    board.upsert(make_job(next_due=100.0, tick=tick, values={"ticks": 0}))

    first = board.due(now=1.0, dt=0.1).messages
    second = board.due(now=1.1, dt=0.1).messages

    assert first == [event]
    assert second == [event]
    assert only_job(board)["values"] == {"ticks": 2}


@pytest.mark.parametrize(
    ("dt", "expected"),
    [
        (0.1, 0.1),
        (MAX_TICK_DT_S, 0.5),
        (10.0, 0.5),
        (-1.0, 0.0),
        (math.nan, 0.0),
        (math.inf, 0.0),
        (-math.inf, 0.0),
    ],
)
def test_tick_dt_is_capped_at_half_a_second_and_non_finite_dt_counts_as_zero(
    dt: float, expected: float
) -> None:
    seen: list[float] = []

    def tick(job: Job, tick_dt: float) -> list[Outgoing]:
        seen.append(tick_dt)
        return []

    board = JobsBoard()
    board.upsert(make_job(tick=tick))

    board.due(now=0.0, dt=dt)

    assert seen == [expected]


# --- order of emissions ---


def test_due_emits_in_insertion_order_with_each_jobs_tick_events_right_before_it() -> None:
    def tick_event(job: Job, dt: float) -> list[Outgoing]:
        return [Outgoing(job.drone_id, "ALERT", None, {"from": job.type}, persistent=True)]

    board = JobsBoard()
    board.upsert(make_job(type="POSITION", tick=tick_event))
    board.upsert(make_job(type="HEARTBEAT"))
    board.upsert(make_job(type="BATTERY", tick=tick_event))
    board.upsert(make_job(type="POSITION", tick=tick_event))  # replaced: keeps its place

    messages = board.due(now=1.0, dt=0.1).messages

    assert [(out.type, out.payload) for out in messages] == [
        ("ALERT", {"from": "POSITION"}),
        ("POSITION", {}),
        ("HEARTBEAT", {}),
        ("ALERT", {"from": "BATTERY"}),
        ("BATTERY", {}),
    ]


def test_removed_and_re_added_job_emits_last() -> None:
    board = JobsBoard()
    board.upsert(make_job(type="HEARTBEAT"))
    board.upsert(make_job(type="BATTERY"))
    board.remove("main-d001", "HEARTBEAT")
    board.upsert(make_job(type="HEARTBEAT"))

    messages = board.due(now=1.0, dt=0.1).messages

    assert [out.type for out in messages] == ["BATTERY", "HEARTBEAT"]


# --- failure isolation ---


def raising_render(job: Job) -> object:
    raise ZeroDivisionError("render bug")


def test_job_with_raising_render_does_not_starve_the_job_after_it_over_10s() -> None:
    clock = FakeClock()
    board = JobsBoard()
    board.upsert(
        Job("main-d001", "BATTERY", period_s=1.0, next_due=1.0, render=raising_render),
    )
    board.upsert(make_job(type="HEARTBEAT", period_s=1.0, next_due=1.0))

    emitted: list[Outgoing] = []
    errors: list[JobError] = []
    for _ in range(100):
        clock.advance(0.1)
        result = board.due(clock.monotonic(), 0.1)
        emitted += result.messages
        errors += result.errors

    assert [out.type for out in emitted] == ["HEARTBEAT"] * 10
    assert len(errors) == 10  # once per period, not on every tick
    assert {error.key for error in errors} == {("main-d001", "BATTERY")}
    assert all(isinstance(error.error, ZeroDivisionError) for error in errors)
    next_due = {job["type"]: job["next_due"] for job in board.snapshot()}
    assert next_due == {"BATTERY": 11.0, "HEARTBEAT": 11.0}


def test_raising_tick_does_not_lose_another_jobs_persistent_event_from_the_same_call() -> None:
    alert = Outgoing("main-d002", "ALERT", None, {"code": "LOW_BATTERY"}, persistent=True)

    def raising_tick(job: Job, dt: float) -> list[Outgoing]:
        raise ValueError("tick bug")

    def alert_tick(job: Job, dt: float) -> list[Outgoing]:
        return [alert]

    board = JobsBoard()
    board.upsert(make_job(drone_id="main-d001", next_due=100.0, tick=raising_tick))
    board.upsert(make_job(drone_id="main-d002", next_due=100.0, tick=alert_tick))

    result = board.due(now=1.0, dt=0.1)

    assert result.messages == [alert]
    assert [(error.key, type(error.error)) for error in result.errors] == [
        (("main-d001", "HEARTBEAT"), ValueError)
    ]


def test_failing_job_drops_its_partial_output_for_that_call() -> None:
    def tick_then_render_fails(job: Job, dt: float) -> list[Outgoing]:
        return [Outgoing(job.drone_id, "ALERT", None, {}, persistent=True)]

    board = JobsBoard()
    board.upsert(
        Job("main-d001", "POSITION", 1.0, 1.0, render=raising_render, tick=tick_then_render_fails)
    )

    result = board.due(now=1.0, dt=0.1)

    assert result.messages == []
    assert len(result.errors) == 1


@pytest.mark.parametrize(
    "returned",
    [None, ({"code": "LOW_BATTERY"},), [{"code": "LOW_BATTERY"}], "ALERT"],
    ids=["none", "tuple", "list-of-dicts", "str"],
)
def test_tick_that_returns_anything_but_a_list_of_outgoing_is_a_failing_job(
    returned: object,
) -> None:
    def bad_tick(job: Job, dt: float) -> Any:
        return returned

    board = JobsBoard()
    board.upsert(make_job(next_due=100.0, tick=bad_tick))

    result = board.due(now=1.0, dt=0.1)

    assert result.messages == []
    (error,) = result.errors
    assert isinstance(error.error, TypeError)
    assert "expected list[Outgoing]" in str(error.error)


# --- payloads are copies ---


def test_emitted_payload_is_a_copy_a_later_tick_does_not_change_it() -> None:
    def live_render(job: Job) -> object:
        return job.values

    def count_tick(job: Job, dt: float) -> list[Outgoing]:
        job.values["pos"]["n"] += 1
        return []

    board = JobsBoard()
    board.upsert(
        Job(
            "main-d001",
            "POSITION",
            1.0,
            1.0,
            render=live_render,
            tick=count_tick,
            values={"pos": {"n": 0}},
        ),
    )

    (out,) = board.due(now=1.0, dt=0.1).messages
    board.due(now=1.1, dt=0.1)
    board.due(now=1.2, dt=0.1)

    assert out.payload == {"pos": {"n": 1}}
    assert only_job(board)["values"] == {"pos": {"n": 3}}
