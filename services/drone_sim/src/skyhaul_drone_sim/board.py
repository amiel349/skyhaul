"""The jobs board: periodic telemetry jobs keyed by `(drone_id, type)`, behind one lock.

Every read and write of job state happens under `JobsBoard._lock`, a plain
`threading.Lock` (not re-entrant, so nested acquisition deadlocks loudly instead of
hiding a design error). Scheduler ticks run under the same lock as command upserts,
so neither can overwrite the other's change. Public reads return copies.

Nothing that can block runs under the lock: no outbox put, sleep, event wait,
network or file I/O, logging, or codec call (design note invariant 6). Tick and
render functions are in-memory only.

Until the drone model lands (#8, #9), tick and render functions receive the job
itself and keep their state in `Job.values`. The wiring PR switches them to the
`Drone`, and `type` to the contract `TelemetryType`.
"""

import copy
import dataclasses
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from skyhaul_drone_sim.outbox import Outgoing

MAX_TICK_DT_S = 0.5
"""A tick never advances state by more than this, however long the scheduler stalled."""

type JobType = str
type JobKey = tuple[str, JobType]
type RenderFn = Callable[[Job], object]
type TickFn = Callable[[Job, float], list[Outgoing]]


@dataclass(slots=True)
class Job:
    drone_id: str
    type: JobType
    period_s: float
    next_due: float
    """Clock.monotonic() seconds at which the job is next emitted."""
    render: RenderFn
    """Builds the payload from the job's current state."""
    tick: TickFn | None = None
    """Runs on every scheduler tick while the job exists. Returns extra events."""
    correlation_id: str | None = None
    values: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> JobKey:
        return (self.drone_id, self.type)


def _copy_job(job: Job) -> Job:
    # render and tick are shared on purpose: they are code, not state.
    return dataclasses.replace(job, values=copy.deepcopy(job.values))


def _upsert(jobs: dict[JobKey, Job], job: Job) -> None:
    if job.period_s <= 0:
        raise ValueError(f"period_s must be positive, got {job.period_s}")
    stored = _copy_job(job)
    existing = jobs.get(job.key)
    if existing is not None:
        stored.next_due = existing.next_due
    jobs[job.key] = stored


def _to_dict(job: Job) -> dict[str, Any]:
    return {
        "drone_id": job.drone_id,
        "type": job.type,
        "period_s": job.period_s,
        "next_due": job.next_due,
        "correlation_id": job.correlation_id,
        "values": copy.deepcopy(job.values),
    }


class World:
    """The board's state while its lock is held.

    Handed out only by `JobsBoard.transaction()` and valid only inside that `with`
    block; any use after it raises. `get` returns the live job so a handler can
    change it in place. Do not keep the reference past the block.
    """

    __slots__ = ("_jobs", "_open")

    def __init__(self, jobs: dict[JobKey, Job]) -> None:
        self._jobs = jobs
        self._open = True

    def get(self, drone_id: str, type: JobType) -> Job | None:
        self._check_open()
        return self._jobs.get((drone_id, type))

    def upsert(self, job: Job) -> None:
        """Store a copy of `job`, replacing any job with the same key but keeping its next_due."""
        self._check_open()
        _upsert(self._jobs, job)

    def remove(self, drone_id: str, type: JobType) -> bool:
        self._check_open()
        return self._jobs.pop((drone_id, type), None) is not None

    def _close(self) -> None:
        self._open = False

    def _check_open(self) -> None:
        if not self._open:
            raise RuntimeError("World used outside its JobsBoard.transaction() block")


class JobsBoard:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[JobKey, Job] = {}

    def upsert(self, job: Job) -> None:
        """Store a copy of `job`, replacing any job with the same key but keeping its next_due."""
        with self._lock:
            _upsert(self._jobs, job)

    def remove(self, drone_id: str, type: JobType) -> bool:
        with self._lock:
            return self._jobs.pop((drone_id, type), None) is not None

    @contextmanager
    def transaction(self) -> Iterator[World]:
        """Hold the lock for the whole `with` block, for check-then-act and handlers."""
        with self._lock:
            world = World(self._jobs)
            try:
                yield world
            finally:
                world._close()

    def due(self, now: float, dt: float) -> list[Outgoing]:
        """Run every job's tick, then emit each job that is due.

        A job is emitted at most once per call, when `now >= next_due`. Its next
        due time then moves one period along its own grid (`next_due += period_s`),
        so a tick that wakes late does not shift later emissions (no drift). If the
        scheduler fell more than a period behind, the next due time restarts at
        `now + period_s`: the job is emitted once, with no catch-up burst. `dt` is
        capped at MAX_TICK_DT_S.

        A tick or render that raises propagates and skips the remaining jobs for
        this call. Isolating a failing job (and logging it outside the lock) is up
        to the scheduler thread, which arrives in the second half of #7.
        """
        dt = min(max(dt, 0.0), MAX_TICK_DT_S)
        out: list[Outgoing] = []
        with self._lock:
            for job in self._jobs.values():
                if job.tick is not None:
                    out.extend(job.tick(job, dt))
                if now >= job.next_due:
                    out.append(
                        Outgoing(
                            drone_id=job.drone_id,
                            type=job.type,
                            correlation_id=job.correlation_id,
                            payload=job.render(job),
                            persistent=False,
                        )
                    )
                    job.next_due += job.period_s
                    if job.next_due <= now:
                        job.next_due = now + job.period_s
        return out

    def snapshot(self) -> list[dict[str, Any]]:
        """Deep copies of every job's data (without its functions)."""
        with self._lock:
            return [_to_dict(job) for job in self._jobs.values()]
