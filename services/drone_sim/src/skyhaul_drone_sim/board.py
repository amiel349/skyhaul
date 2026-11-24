"""The jobs board: periodic telemetry jobs keyed by `(drone_id, type)`, behind one lock.

Every read and write of job state happens under `JobsBoard._lock`, a plain
`threading.Lock`. Nested acquisition is forbidden: calling a public board method
from a handler inside `transaction()`, or from a tick or render, would see or
change state mid-transaction. A re-entrant lock would allow that silently, and a
plain lock would hang. So the board records the thread that holds the lock and
raises RuntimeError when that thread asks again: the handler gets an
INTERNAL_ERROR NACK, the tick a JobError. Use the `World` or job you were given.

Scheduler ticks run under the same lock as command upserts, so neither can
overwrite the other's change. Public reads return copies.

Nothing that can block runs under the lock: no outbox put, sleep, event wait,
network or file I/O, logging, or codec call (design note invariant 6). Tick and
render functions are in-memory only.

A `Job` is frozen: its identity (`drone_id`, `type`), period and correlation id
cannot change in place, so the board's key always matches the job and every
period was validated when the job was built. To change them, upsert a new `Job`
(`dataclasses.replace`). Only the contents of `Job.values` change in place. The
board keeps each job's actual next due time itself, next to the job.

Until the drone model lands (#8, #9), tick and render functions receive the job
itself and keep their state in `Job.values`, and the board knows drones only as a
set of ids (`add_drone`). #9 replaces both with the `Drone` model.
"""

import copy
import dataclasses
import math
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from skyhaul_contracts import MessageType, TelemetryType

from skyhaul_drone_sim.outbox import Outgoing, is_outgoing_list, new_message

MAX_TICK_DT_S = 0.5
"""A tick never advances state by more than this, however long the scheduler stalled."""

type JobType = TelemetryType
type JobKey = tuple[str, JobType]
type RenderFn = Callable[[Job], BaseModel]
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
    """Builds the payload model for `type` from the job's current state."""
    tick: TickFn | None = None
    """Runs on every scheduler tick while the job exists. Returns extra events.
    It may change `values` in place, nothing else."""
    correlation_id: str | None = None
    values: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Normalize "HEARTBEAT" to TelemetryType.HEARTBEAT; an unknown type raises here.
        object.__setattr__(self, "type", TelemetryType(self.type))
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
    """Store `job` (the caller passes a private copy). See JobsBoard.upsert for next_due."""
    existing = jobs.get(job.key)
    if existing is None:
        jobs[job.key] = _Entry(job, job.next_due)
        return
    if job.period_s != existing.job.period_s:
        existing.next_due = min(existing.next_due, job.next_due)
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
            dataclasses.replace(event, message=copy.deepcopy(event.message)) for event in events
        )
    if is_due:
        # A copy, so the publisher never reads live job state. Building the message
        # validates the payload against the job's type (no codec lock is taken).
        payload = copy.deepcopy(job.render(job))
        message = new_message(MessageType(job.type), job.drone_id, payload, job.correlation_id)
        produced.append(Outgoing(message, persistent=False))
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
        "type": job.type.value,
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

    __slots__ = ("_drones", "_jobs", "_open", "_staged", "_unknown_drone_commands")

    def __init__(self, jobs: dict[JobKey, _Entry], drones: set[str]) -> None:
        self._jobs = jobs
        self._drones = drones
        self._open = True
        self._staged: dict[JobKey, Job | None] = {}
        self._unknown_drone_commands = 0

    def has_drone(self, drone_id: str) -> bool:
        self._check_open()
        return drone_id in self._drones

    def count_unknown_drone_command(self) -> None:
        """Stage one more command for a drone that does not exist (kept on discard)."""
        self._check_open()
        self._unknown_drone_commands += 1

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
        """Stage a copy of `job`. Same next_due rule as JobsBoard.upsert."""
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
        # Ident of the thread holding the lock. A thread can only find its own
        # ident here while it holds the lock, so reading it unlocked is safe.
        self._owner: int | None = None
        self._jobs: dict[JobKey, _Entry] = {}
        self._drones: set[str] = set()
        self._unknown_drone_commands = 0

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Take the lock, or raise if this thread already holds it (instead of hanging)."""
        me = threading.get_ident()
        if self._owner == me:
            raise RuntimeError(
                "JobsBoard lock taken again by the thread that holds it: inside a "
                "transaction, tick or render, use the World or job you were given"
            )
        with self._lock:
            self._owner = me
            try:
                yield
            finally:
                self._owner = None

    def upsert(self, job: Job) -> None:
        """Store a copy of `job`, replacing any job with the same key.

        A new job is first due at `job.next_due`. A replaced job with the same
        period keeps its next due time, so re-sending a job does not shift it. If
        the period changed, the earlier of the old next due time and
        `job.next_due` wins, so a faster rate starts when the caller asked.
        """
        with self._locked():
            _upsert(self._jobs, _copy_job(job))

    def remove(self, drone_id: str, type: JobType) -> bool:
        with self._locked():
            return self._jobs.pop((drone_id, type), None) is not None

    @contextmanager
    def transaction(self) -> Iterator[World]:
        """Hold the lock for the whole `with` block, for check-then-act and handlers.

        The World's staged changes are committed when the block ends normally and
        dropped when it raises.
        """
        with self._locked():
            world = World(self._jobs, self._drones)
            try:
                yield world
                world._commit()
            finally:
                self._unknown_drone_commands += world._unknown_drone_commands
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

        Raises ValueError if `now` is not finite (a broken clock), before touching
        any job.
        """
        if not math.isfinite(now):
            raise ValueError(f"now must be finite, got {now}")
        dt = min(max(dt, 0.0), MAX_TICK_DT_S) if math.isfinite(dt) else 0.0
        messages: list[Outgoing] = []
        errors: list[JobError] = []
        with self._locked():
            for key, entry in self._jobs.items():
                is_due = now >= entry.next_due
                try:
                    messages.extend(_run_job(entry.job, dt, is_due))
                except Exception as exc:
                    errors.append(JobError(key, exc))
                if is_due:
                    _advance(entry, now)
        return DueResult(messages, errors)

    def add_drone(self, drone_id: str) -> None:
        """Make commands for `drone_id` reach their handler. Stand-in for #9's spawn."""
        with self._locked():
            self._drones.add(drone_id)

    def unknown_drone_commands(self) -> int:
        """Commands received for drones that do not exist, since the process started."""
        with self._locked():
            return self._unknown_drone_commands

    def snapshot(self) -> list[dict[str, Any]]:
        """Deep copies of every job's data (without its functions)."""
        with self._locked():
            return [_to_dict(entry) for entry in self._jobs.values()]
