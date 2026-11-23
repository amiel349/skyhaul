"""The jobs board: periodic telemetry jobs keyed by `(drone_id, type)`, behind one lock.

Every read and write of job state happens under `JobsBoard._lock`, a plain
`threading.Lock`. Nested acquisition is forbidden: the lock is not re-entrant, so a
thread that takes it again (for example, calling `board.upsert` from a handler
inside `transaction()`) hangs forever, silently. Use the `World` you were given
instead. A re-entrant lock would hide such a call and let one thread see half-done
changes. Scheduler ticks run under the same lock as command upserts, so neither
can overwrite the other's change. Public reads return copies.

Nothing that can block runs under the lock: no outbox put, sleep, event wait,
network or file I/O, logging, or codec call (design note invariant 6). Tick and
render functions are in-memory only.

A `Job` is frozen: its identity (`drone_id`, `type`), period and correlation id
cannot change in place, so the board's key always matches the job and every
period was validated when the job was built. To change them, upsert a new `Job`
(`dataclasses.replace`). Only the contents of `Job.values` change in place. The
board keeps each job's actual next due time itself, next to the job.

Until the drone model lands (#8, #9), tick and render functions receive the job
itself and keep their state in `Job.values`. The wiring PR switches them to the
`Drone`, and `type` to the contract `TelemetryType`.
"""

import copy
import dataclasses
import math
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from skyhaul_drone_sim.outbox import Outgoing, is_outgoing_list

MAX_TICK_DT_S = 0.5
"""A tick never advances state by more than this, however long the scheduler stalled."""

type JobType = str
type JobKey = tuple[str, JobType]
type RenderFn = Callable[[Job], object]
type TickFn = Callable[[Job, float], list[Outgoing]]


@dataclass(frozen=True, slots=True)
class Job:
    drone_id: str
    type: JobType
    period_s: float
    next_due: float
    """Clock.monotonic() seconds of the first emission when the job is added.

    Once the job is on the board, the board tracks the actual next due time;
    `World.get` returns a job carrying it."""
    render: RenderFn
    """Builds the payload from the job's current state."""
    tick: TickFn | None = None
    """Runs on every scheduler tick while the job exists. Returns extra events.
    It may change `values` in place, nothing else."""
    correlation_id: str | None = None
    values: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (math.isfinite(self.period_s) and self.period_s > 0):
            raise ValueError(f"period_s must be finite and positive, got {self.period_s}")
        if not math.isfinite(self.next_due):
            raise ValueError(f"next_due must be finite, got {self.next_due}")

    @property
    def key(self) -> JobKey:
        return (self.drone_id, self.type)


@dataclass(slots=True)
class _Entry:
    """A job on the board and its actual next due time."""

    job: Job
    next_due: float


@dataclass(frozen=True, slots=True)
class JobError:
    """A job whose tick or render failed during one `due()` call."""

    key: JobKey
    error: Exception


@dataclass(frozen=True, slots=True)
class DueResult:
    """What one `due()` call produced. The scheduler logs `errors` outside the lock."""

    messages: list[Outgoing]
    errors: list[JobError]


def _copy_job(job: Job) -> Job:
    # render and tick are shared on purpose: they are code, not state.
    return dataclasses.replace(job, values=copy.deepcopy(job.values))


def _upsert(jobs: dict[JobKey, _Entry], job: Job) -> None:
    """Store `job` (the caller passes a private copy), keeping next_due if the key exists."""
    existing = jobs.get(job.key)
    if existing is None:
        jobs[job.key] = _Entry(job, job.next_due)
    else:
        existing.job = job


def _run_job(job: Job, dt: float, is_due: bool) -> list[Outgoing]:
    """Tick the job, then render it if it is due. Raises if the tick or render fails."""
    produced: list[Outgoing] = []
    if job.tick is not None:
        events = job.tick(job, dt)
        if not is_outgoing_list(events):
            raise TypeError(f"tick returned {type(events).__name__}, expected list[Outgoing]")
        # Copies, so the publisher never reads live job state.
        produced.extend(
            dataclasses.replace(event, payload=copy.deepcopy(event.payload)) for event in events
        )
    if is_due:
        produced.append(
            Outgoing(
                drone_id=job.drone_id,
                type=job.type,
                correlation_id=job.correlation_id,
                # A copy, so the publisher never reads live job state.
                payload=copy.deepcopy(job.render(job)),
                persistent=False,
            )
        )
    return produced


def _advance(entry: _Entry, now: float) -> None:
    """Move next_due to the first slot on the job's own grid that is after `now`."""
    period_s = entry.job.period_s
    missed = math.floor((now - entry.next_due) / period_s)
    entry.next_due += period_s * (missed + 1)
    if entry.next_due <= now:  # float rounding put it on `now` itself
        entry.next_due += period_s


def _to_dict(entry: _Entry) -> dict[str, Any]:
    job = entry.job
    return {
        "drone_id": job.drone_id,
        "type": job.type,
        "period_s": job.period_s,
        "next_due": entry.next_due,
        "correlation_id": job.correlation_id,
        "values": copy.deepcopy(job.values),
    }


class World:
    """A transaction on the board, used while its lock is held.

    Handed out only by `JobsBoard.transaction()` and valid only inside that `with`
    block; any use after it raises. Changes are staged (copy-on-write): `get`
    returns a private copy of the job, carrying the board's actual next due time,
    and changes to its `values`, `upsert` and `remove` reach the board only when
    the block ends normally. If the block raises, or `discard()` is called, none
    of them do.
    """

    __slots__ = ("_jobs", "_open", "_staged")

    def __init__(self, jobs: dict[JobKey, _Entry]) -> None:
        self._jobs = jobs
        self._open = True
        self._staged: dict[JobKey, Job | None] = {}

    def get(self, drone_id: str, type: JobType) -> Job | None:
        self._check_open()
        key = (drone_id, type)
        if key in self._staged:
            return self._staged[key]
        entry = self._jobs.get(key)
        if entry is None:
            return None
        staged = dataclasses.replace(
            entry.job, values=copy.deepcopy(entry.job.values), next_due=entry.next_due
        )
        self._staged[key] = staged
        return staged

    def upsert(self, job: Job) -> None:
        """Stage a copy of `job`, replacing any job with the same key but keeping its next_due."""
        self._check_open()
        self._staged[job.key] = _copy_job(job)

    def remove(self, drone_id: str, type: JobType) -> bool:
        self._check_open()
        existed = self.get(drone_id, type) is not None
        self._staged[(drone_id, type)] = None
        return existed

    def discard(self) -> None:
        """Drop every change staged in this transaction so far."""
        self._check_open()
        self._staged.clear()

    def _commit(self) -> None:
        for key, job in self._staged.items():
            if job is None:
                self._jobs.pop(key, None)
            else:
                _upsert(self._jobs, job)
        self._staged.clear()

    def _close(self) -> None:
        self._open = False

    def _check_open(self) -> None:
        if not self._open:
            raise RuntimeError("World used outside its JobsBoard.transaction() block")


class JobsBoard:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[JobKey, _Entry] = {}

    def upsert(self, job: Job) -> None:
        """Store a copy of `job`, replacing any job with the same key but keeping its next_due."""
        with self._lock:
            _upsert(self._jobs, _copy_job(job))

    def remove(self, drone_id: str, type: JobType) -> bool:
        with self._lock:
            return self._jobs.pop((drone_id, type), None) is not None

    @contextmanager
    def transaction(self) -> Iterator[World]:
        """Hold the lock for the whole `with` block, for check-then-act and handlers.

        The World's staged changes are committed when the block ends normally and
        dropped when it raises.
        """
        with self._lock:
            world = World(self._jobs)
            try:
                yield world
                world._commit()
            finally:
                world._close()

    def due(self, now: float, dt: float) -> DueResult:
        """Run every job's tick, then emit each job that is due.

        A job is emitted at most once per call, when `now >= next_due`. Its next
        due time then moves to the first slot on its own grid that is after `now`
        (`next_due + k * period_s`). So a tick that wakes late does not shift later
        emissions (no drift), and after a stall the missed slots are skipped: the
        job is emitted once, with no catch-up burst, and keeps its own phase, so
        jobs do not all bunch onto the same tick. `dt` is capped at
        MAX_TICK_DT_S, and a non-finite `dt` counts as 0.

        Order: jobs are handled in the order they were first added (a replaced job
        keeps its place, a removed and re-added one goes last). Each job's tick
        events come right before its own emission.

        A job whose tick or render raises, or whose tick returns anything but a
        list of Outgoing, is reported in `errors` and its output for this call is
        dropped. Its next_due still advances if it was due, so it fails at most once
        per period. Other jobs are not affected.
        """
        dt = min(max(dt, 0.0), MAX_TICK_DT_S) if math.isfinite(dt) else 0.0
        messages: list[Outgoing] = []
        errors: list[JobError] = []
        with self._lock:
            for key, entry in self._jobs.items():
                is_due = now >= entry.next_due
                try:
                    messages.extend(_run_job(entry.job, dt, is_due))
                except Exception as exc:
                    errors.append(JobError(key, exc))
                if is_due:
                    _advance(entry, now)
        return DueResult(messages, errors)

    def snapshot(self) -> list[dict[str, Any]]:
        """Deep copies of every job's data (without its functions)."""
        with self._lock:
            return [_to_dict(entry) for entry in self._jobs.values()]
