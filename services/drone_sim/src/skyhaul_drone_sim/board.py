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

Drones live on the board too (`model.Drone`, by id). A job's tick and render get the
drone it belongs to as well as the job: ticks move the drone and drain its battery,
renders read it. A job may say when it is finished (`done`): it then emits one final
message and leaves the board, in the same `due()` call.

Namespaces (ADR 0009): every drone id is `<namespace>-dNNN`. The board keeps an id counter
and an epoch per namespace. `reset(ns)` removes the namespace's drones and jobs (their
dedup caches live on the drones), resets its counter and bumps its epoch, all under the
lock. Every message leaving the board is stamped with its namespace's current epoch while
the lock is held, and the publisher sends it only if `is_current` still holds, so nothing
produced before a reset is published after it.

Faults (#10, `faults.py`) live on the drones, so they change under the same lock. `due()`
expires them first on every tick, then emits the delayed replies that fell due (REPLY_DELAY),
then runs the jobs. A drone in LINK_LOSS keeps ticking (it moves and drains), but nothing it
produces is emitted. Delayed replies are stamped with their namespace epoch when they are
scheduled and keep that stamp; at most MAX_DELAYED_PER_NAMESPACE wait per namespace, and
`reset(ns)` drops the namespace's waiting ones.
"""

import copy
import dataclasses
import heapq
import itertools
import math
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Final

from pydantic import BaseModel
from skyhaul_contracts import MessageType, TelemetryType

from skyhaul_drone_sim.faults import Fault, apply_fault, clear_faults, link_lost, tick_faults
from skyhaul_drone_sim.model import Drone
from skyhaul_drone_sim.namespaces import MAX_DRONES_PER_NAMESPACE, namespace_of, nth_drone_id
from skyhaul_drone_sim.outbox import UNSTAMPED, Outgoing, is_outgoing_list, new_message

MAX_TICK_DT_S = 0.5
"""A tick never advances state by more than this, however long the scheduler stalled."""

MAX_DELAYED_PER_NAMESPACE: Final = 1000
"""Delayed replies (REPLY_DELAY) waiting per namespace. One more gets NACK INTERNAL_ERROR."""

type JobType = TelemetryType
type JobKey = tuple[str, JobType]
type RenderFn = Callable[[Drone, Job, float], BaseModel]
"""`render(drone, job, now)`: the payload from the drone's current state."""
type TickFn = Callable[[Drone, Job, float], list[Outgoing]]
"""`tick(drone, job, dt)`: advance the drone by `dt` seconds; returns events (ALERTs)."""
type DoneFn = Callable[[Drone], bool]
type MakeDrone = Callable[[str], tuple[Drone, list[Job]]]
"""`make(drone_id)`: a new drone and its jobs. Runs under the board lock: in memory only."""


class SpawnConflict(Exception):
    """The ids a spawn would create already exist, or the namespace has no ids left."""


@dataclass(frozen=True, slots=True)
class ResetResult:
    namespace: str
    drones_removed: int
    jobs_removed: int
    epoch: int
    """The namespace's new epoch."""


@dataclass(frozen=True, slots=True)
class JobView:
    """A job as the control API shows it."""

    type: JobType
    period_s: float
    next_due: float


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
    It may change the drone and the job's `values` in place, nothing else."""
    correlation_id: str | None = None
    values: dict[str, Any] = field(default_factory=dict)
    done: DoneFn | None = None
    """Checked after the tick. Once it returns True the job emits one final message in
    that same `due()` call, due or not, and is removed (POSITION on touchdown,
    MISSION_STATUS on COMPLETED or ABORTED)."""
    final_persistent: bool = False
    """Whether that final message is persistent. Periodic emissions never are."""

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


def _run_job(
    drone: Drone, job: Job, now: float, dt: float, is_due: bool
) -> tuple[list[Outgoing], bool]:
    """Tick the job, then render it if it is due or done. Returns the messages and whether
    the job is done. Raises if the tick, the done check or the render fails."""
    produced: list[Outgoing] = []
    if job.tick is not None:
        events = job.tick(drone, job, dt)
        if not is_outgoing_list(events):
            raise TypeError(f"tick returned {type(events).__name__}, expected list[Outgoing]")
        # Copies, so the publisher never reads live job state.
        produced.extend(
            dataclasses.replace(event, message=copy.deepcopy(event.message)) for event in events
        )
    done = job.done is not None and job.done(drone)
    if is_due or done:
        produced.append(render_job(drone, job, now, persistent=done and job.final_persistent))
    return produced, done


def render_job(drone: Drone, job: Job, now: float, *, persistent: bool = False) -> Outgoing:
    """The job's message for the drone's current state, with the job's correlation_id."""
    # A copy, so the publisher never reads live job state. Building the message
    # validates the payload against the job's type (no codec lock is taken).
    payload = copy.deepcopy(job.render(drone, job, now))
    message = new_message(MessageType(job.type), job.drone_id, payload, job.correlation_id)
    return Outgoing(message, persistent=persistent)


def _advance(entry: _Entry, now: float) -> None:
    """Move next_due to the first slot on the job's own grid that is after `now`."""
    period_s = entry.job.period_s
    missed = math.floor((now - entry.next_due) / period_s)
    entry.next_due += period_s * (missed + 1)
    if entry.next_due <= now:  # float rounding put it on `now` itself
        entry.next_due += period_s


def _stamp(epochs: dict[str, int], out: Outgoing) -> Outgoing:
    """`out` marked with the current epoch of its drone's namespace. Call under the lock."""
    namespace = namespace_of(out.message.drone_id)
    return dataclasses.replace(out, namespace=namespace, epoch=epochs.get(namespace, 0))


class _DelayedReplies:
    """Replies waiting for their REPLY_DELAY to pass: a heap by due time, plus a count per
    namespace. Not locked itself: used only under the board lock."""

    def __init__(self) -> None:
        self._heap: list[tuple[float, int, Outgoing]] = []
        self._order = itertools.count()  # FIFO among replies due at the same time
        self._per_namespace: dict[str, int] = {}

    def count(self, namespace: str) -> int:
        return self._per_namespace.get(namespace, 0)

    def push(self, at: float, out: Outgoing) -> None:
        heapq.heappush(self._heap, (at, next(self._order), out))
        self._per_namespace[out.namespace] = self.count(out.namespace) + 1

    def pop_due(self, now: float) -> list[Outgoing]:
        due: list[Outgoing] = []
        while self._heap and self._heap[0][0] <= now:
            _, _, out = heapq.heappop(self._heap)
            self._per_namespace[out.namespace] -= 1
            due.append(out)
        return due

    def drop_namespace(self, namespace: str) -> None:
        self._heap = [item for item in self._heap if item[2].namespace != namespace]
        heapq.heapify(self._heap)
        self._per_namespace.pop(namespace, None)


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

    __slots__ = (
        "_delayed",
        "_drones",
        "_epochs",
        "_jobs",
        "_open",
        "_staged",
        "_staged_delayed",
        "_staged_drones",
        "_unknown_drone_commands",
    )

    def __init__(
        self,
        jobs: dict[JobKey, _Entry],
        drones: dict[str, Drone],
        epochs: dict[str, int],
        delayed: _DelayedReplies,
    ) -> None:
        self._jobs = jobs
        self._drones = drones
        self._epochs = epochs
        self._delayed = delayed
        self._open = True
        self._staged: dict[JobKey, Job | None] = {}
        self._staged_drones: dict[str, Drone] = {}
        self._staged_delayed: list[tuple[float, Outgoing]] = []
        self._unknown_drone_commands = 0

    def has_drone(self, drone_id: str) -> bool:
        self._check_open()
        return drone_id in self._staged_drones or drone_id in self._drones

    def drone(self, drone_id: str) -> Drone | None:
        """A private copy of the drone; changes to it are staged like job changes."""
        self._check_open()
        staged = self._staged_drones.get(drone_id)
        if staged is not None:
            return staged
        live = self._drones.get(drone_id)
        if live is None:
            return None
        staged = live.copy()
        self._staged_drones[drone_id] = staged
        return staged

    def add_drone(self, drone: Drone) -> None:
        """Stage a new drone (or replace one with the same id)."""
        self._check_open()
        self._staged_drones[drone.drone_id] = drone.copy()

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

    def stamp(self, messages: list[Outgoing]) -> list[Outgoing]:
        """The messages marked with their namespace's current epoch (ADR 0009). The
        consumer stamps a dispatch's replies before the transaction ends."""
        self._check_open()
        return [_stamp(self._epochs, out) for out in messages]

    def can_delay(self, namespace: str, count: int = 1) -> bool:
        """True if `count` more delayed replies fit in the namespace's bound."""
        self._check_open()
        staged = sum(1 for _, out in self._staged_delayed if out.namespace == namespace)
        waiting = self._delayed.count(namespace) + staged
        return waiting + count <= MAX_DELAYED_PER_NAMESPACE

    def delay(self, at: float, messages: list[Outgoing]) -> None:
        """Stage the messages to be emitted by the first `due(now)` with `now >= at`. They
        are stamped with their namespace's current epoch now, and keep that stamp."""
        self._check_open()
        self._staged_delayed += [(at, _stamp(self._epochs, out)) for out in messages]

    def discard(self) -> None:
        """Drop every change staged in this transaction so far."""
        self._check_open()
        self._staged.clear()
        self._staged_drones.clear()
        self._staged_delayed.clear()

    def _commit(self) -> None:
        self._drones.update(self._staged_drones)
        self._staged_drones.clear()
        for at, out in self._staged_delayed:
            self._delayed.push(at, out)
        self._staged_delayed.clear()
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
        self._drones: dict[str, Drone] = {}
        self._unknown_drone_commands = 0
        self._epochs: dict[str, int] = {}
        """Per namespace; a namespace never reset is at epoch 0."""
        self._spawned: dict[str, int] = {}
        """Per namespace: how many ids `spawn` has handed out since the last reset."""
        self._delayed = _DelayedReplies()

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
            world = World(self._jobs, self._drones, self._epochs, self._delayed)
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

        A job whose `done(drone)` is true after its tick emits one final message in
        this call, due or not (persistent if `final_persistent`), and is removed. A job
        whose drone is not on the board is reported in `errors` and removed.

        Before the jobs, every drone's faults are ticked (expiry, LINK_RESTORED, GPS
        drift), then the delayed replies due by `now` are emitted with the stamp they got
        when scheduled. Nothing a drone in LINK_LOSS produces is emitted: not its job
        messages or tick events, and not its delayed replies (they are dropped).

        Raises ValueError if `now` is not finite (a broken clock), before touching
        any job.
        """
        if not math.isfinite(now):
            raise ValueError(f"now must be finite, got {now}")
        dt = min(max(dt, 0.0), MAX_TICK_DT_S) if math.isfinite(dt) else 0.0
        messages: list[Outgoing] = []
        errors: list[JobError] = []
        with self._locked():
            for each in self._drones.values():
                messages.extend(tick_faults(each, now))
            delayed = [out for out in self._delayed.pop_due(now) if self._deliverable(out)]
            finished: list[JobKey] = []
            for key, entry in self._jobs.items():
                is_due = now >= entry.next_due
                drone = self._drones.get(entry.job.drone_id)
                if drone is None:  # cannot happen through spawn and reset; never tick it
                    errors.append(JobError(key, LookupError("job for a drone not on the board")))
                    finished.append(key)
                    continue
                try:
                    produced, done = _run_job(drone, entry.job, now, dt, is_due)
                    if not link_lost(drone):
                        messages.extend(produced)
                    if done:
                        finished.append(key)
                except Exception as exc:
                    errors.append(JobError(key, exc))
                if is_due:
                    _advance(entry, now)
            for key in finished:
                del self._jobs[key]
            messages = [_stamp(self._epochs, out) for out in messages] + delayed
        return DueResult(messages, errors)

    def _deliverable(self, out: Outgoing) -> bool:
        """A delayed reply goes out only if it is stamped with its namespace's current epoch
        and its drone is still on the board and not in LINK_LOSS. Call under the lock."""
        if out.epoch == UNSTAMPED or self._epochs.get(out.namespace, 0) != out.epoch:
            return False
        drone = self._drones.get(out.message.drone_id)
        return drone is not None and not link_lost(drone)

    def add_fault(self, drone_id: str, fault: Fault) -> bool:
        """Put `fault` on the drone, replacing one of the same kind. False if no such drone."""
        with self._locked():
            drone = self._drones.get(drone_id)
            if drone is None:
                return False
            apply_fault(drone, fault)
            return True

    def clear_faults(self, drone_id: str) -> bool:
        """Remove all of the drone's faults, with no LINK_RESTORED. False if no such drone."""
        with self._locked():
            drone = self._drones.get(drone_id)
            if drone is None:
                return False
            clear_faults(drone)
            return True

    def add_drone(self, drone: Drone) -> None:
        """Put a bare drone on the board (no jobs), for tests. `spawn` is the real way."""
        with self._locked():
            self._drones[drone.drone_id] = drone.copy()

    def spawn(self, namespace: str, count: int, make: MakeDrone) -> list[str]:
        """Create the namespace's next `count` drones and their jobs, all or nothing.

        Ids continue from the namespace's counter (`<ns>-d001` after a reset). Raises
        SpawnConflict, changing nothing, if one of them already exists (a drone seeded at
        startup) or the namespace would pass MAX_DRONES_PER_NAMESPACE ids.
        """
        if count < 1:
            raise ValueError("count must be at least 1")
        with self._locked():
            first = self._spawned.get(namespace, 0) + 1
            if first + count - 1 > MAX_DRONES_PER_NAMESPACE:
                raise SpawnConflict(f"namespace {namespace} has no free drone ids left")
            ids = [nth_drone_id(namespace, index) for index in range(first, first + count)]
            if any(new_id in self._drones for new_id in ids):
                raise SpawnConflict("drone ids already exist")
            built = [make(new_id) for new_id in ids]
            for drone, jobs in built:
                self._drones[drone.drone_id] = drone
                for job in jobs:
                    _upsert(self._jobs, _copy_job(job))
            self._spawned[namespace] = first + count - 1
            return ids

    def remove_drone(self, drone_id: str) -> bool:
        """Remove a drone and its jobs. False if there was no such drone."""
        with self._locked():
            if self._drones.pop(drone_id, None) is None:
                return False
            for key in [key for key in self._jobs if key[0] == drone_id]:
                del self._jobs[key]
            return True

    def reset(self, namespace: str) -> ResetResult:
        """Atomically remove every drone and job of `namespace` (with their faults), drop its
        waiting delayed replies, reset its id counter and bump its epoch. Other namespaces
        are untouched. An empty namespace is fine. A drone in LINK_LOSS is simply gone, so
        no LINK_RESTORED is sent for it."""
        prefix = namespace + "-"
        with self._locked():
            drones = [key for key in self._drones if key.startswith(prefix)]
            jobs = [key for key in self._jobs if key[0].startswith(prefix)]
            for drone_key in drones:
                del self._drones[drone_key]
            for job_key in jobs:
                del self._jobs[job_key]
            self._spawned.pop(namespace, None)
            self._delayed.drop_namespace(namespace)
            epoch = self._epochs.get(namespace, 0) + 1
            self._epochs[namespace] = epoch
            return ResetResult(namespace, len(drones), len(jobs), epoch)

    def is_current(self, namespace: str, epoch: int) -> bool:
        """True if `epoch` is still the namespace's epoch (no reset since it was stamped)."""
        with self._locked():
            return self._epochs.get(namespace, 0) == epoch

    def stamp(self, out: Outgoing) -> Outgoing:
        """`out` marked with its namespace's current epoch, for messages made off the board
        (the consumer's NACKs for commands it could not decode)."""
        with self._locked():
            return _stamp(self._epochs, out)

    def drones(self, namespace: str) -> list[Drone]:
        """Copies of the namespace's drones, by id."""
        prefix = namespace + "-"
        with self._locked():
            return [
                self._drones[key].copy() for key in sorted(self._drones) if key.startswith(prefix)
            ]

    def drone_with_jobs(self, drone_id: str) -> tuple[Drone, list[JobView]] | None:
        """A copy of the drone and its jobs, or None."""
        with self._locked():
            live = self._drones.get(drone_id)
            if live is None:
                return None
            jobs = [
                JobView(entry.job.type, entry.job.period_s, entry.next_due)
                for key, entry in self._jobs.items()
                if key[0] == drone_id
            ]
            return live.copy(), jobs

    def drone(self, drone_id: str) -> Drone | None:
        """A copy of the drone's current state, or None."""
        with self._locked():
            live = self._drones.get(drone_id)
            return None if live is None else live.copy()

    def drone_count(self) -> int:
        with self._locked():
            return len(self._drones)

    def unknown_drone_commands(self) -> int:
        """Commands received for drones that do not exist, since the process started."""
        with self._locked():
            return self._unknown_drone_commands

    def snapshot(self) -> list[dict[str, Any]]:
        """Deep copies of every job's data (without its functions)."""
        with self._locked():
            return [_to_dict(entry) for entry in self._jobs.values()]
