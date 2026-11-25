"""Periodic jobs on the consumer thread.

- Telemetry retention (ADR 0012): hourly, rows with ``ts`` older than the retention are
  deleted in batches of 10,000, one batch per loop pass, so consumption keeps flowing while
  a backlog is cleared.
- The command sweeper (m2-design section 6): every second, SENT for longer than the command
  timeout (10 s) becomes TIMED_OUT and PENDING for longer than 30 s becomes FAILED. Each
  change is published as ``command.updated``. In the same pass a mission DISPATCHING for
  longer than 60 s without ACTIVE becomes FAILED (``mission.updated``).
- The link watchdog (m2-design section 8): derived LINK_LOST alerts, see ``LinkWatchdog``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import redis
from skyhaul_fleet_core.commands import (
    COMMAND_TIMEOUT_S,
    COMMAND_UPDATED,
    PENDING_TIMEOUT_S,
    command_json,
)
from skyhaul_fleet_core.missions import (
    ALERT_RAISED,
    ALERT_UPDATED,
    LINK_LOSS_AFTER_S,
    MISSION_DISPATCH_TIMEOUT_S,
    MISSION_UPDATED,
    alert_json,
    mission_json,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from skyhaul_ingestor import store
from skyhaul_ingestor.state import StateWriter

RETENTION_INTERVAL_S: Final = 3600.0
RETRY_AFTER_ERROR_S: Final = 60.0
SWEEP_INTERVAL_S: Final = 1.0

log = logging.getLogger(__name__)


class Retention:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        days: int,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        batch_size: int = store.RETENTION_BATCH,
    ) -> None:
        self._sessions = sessions
        self._keep = timedelta(days=days)
        self._wall_clock = wall_clock
        self._batch_size = batch_size
        self._next_run = 0.0

    def maybe_run(self, now: float) -> int:
        """Delete one batch if a run is due. Returns the rows deleted."""
        if now < self._next_run:
            return 0
        cutoff = self._wall_clock() - self._keep
        try:
            with self._sessions.begin() as session:
                deleted = store.delete_expired(session, cutoff, self._batch_size)
        except SQLAlchemyError as exc:
            log.warning("retention failed (%s), retrying later", type(exc).__name__)
            self._next_run = now + RETRY_AFTER_ERROR_S
            return 0
        if deleted < self._batch_size:
            self._next_run = now + RETENTION_INTERVAL_S
        if deleted:
            log.info("retention deleted old telemetry", extra={"deleted": deleted})
        return deleted


class CommandSweeper:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        state: StateWriter,
        timeout_s: int = COMMAND_TIMEOUT_S,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        dispatch_timeout_s: int = MISSION_DISPATCH_TIMEOUT_S,
    ) -> None:
        self._sessions = sessions
        self._state = state
        self._timeout = timedelta(seconds=timeout_s)
        self._pending_timeout = timedelta(seconds=PENDING_TIMEOUT_S)
        self._dispatch = timedelta(seconds=dispatch_timeout_s)
        self._wall_clock = wall_clock
        self._next_run = 0.0

    def maybe_run(self, now: float) -> int:
        """Sweep if a run is due. Returns the number of commands and missions moved."""
        if now < self._next_run:
            return 0
        self._next_run = now + SWEEP_INTERVAL_S
        wall = self._wall_clock()
        try:
            with self._sessions.begin() as session:
                moved = store.sweep_commands(
                    session,
                    sent_before=wall - self._timeout,
                    created_before=wall - self._pending_timeout,
                )
                failed = store.sweep_missions(session, started_before=wall - self._dispatch)
                events: list[dict[str, Any]] = [
                    {"event": COMMAND_UPDATED, "data": command_json(c)} for c in moved
                ]
                events += [{"event": MISSION_UPDATED, "data": mission_json(m)} for m in failed]
        except SQLAlchemyError as exc:
            log.warning("command sweep failed (%s), retrying", type(exc).__name__)
            return 0
        _publish(self._state, events, "command sweep")
        if moved or failed:
            log.info("command sweep", extra={"moved": len(moved), "missions_failed": len(failed)})
        return len(moved) + len(failed)


class LinkDetector:
    """Which watched drones went silent and which came back, on a monotonic clock. Pure
    bookkeeping: the ``LinkWatchdog`` does the writes. Only drones that sent a heartbeat (or
    were seeded) are watched."""

    def __init__(self, after_s: float = LINK_LOSS_AFTER_S) -> None:
        self.after_s = after_s
        self._last: dict[str, float] = {}
        self._lost: set[str] = set()
        self._back: set[str] = set()

    def heard(self, drone_ids: Iterable[str], now: float) -> None:
        for drone_id in drone_ids:
            self._last[drone_id] = max(now, self._last.get(drone_id, now))
            if drone_id in self._lost:
                self._back.add(drone_id)

    def seed_lost(self, drone_ids: Iterable[str]) -> None:
        """Drones with an open LINK_LOST from before a restart: the next heartbeat clears."""
        self._lost.update(drone_ids)

    def silent(self, now: float) -> list[str]:
        """Watched drones without a heartbeat for ``after_s`` that are not lost yet."""
        return sorted(
            drone_id
            for drone_id, heard_at in self._last.items()
            if drone_id not in self._lost and now - heard_at >= self.after_s
        )

    def returned(self) -> list[str]:
        return sorted(self._back)

    def mark_lost(self, drone_id: str) -> None:
        self._lost.add(drone_id)
        self._back.discard(drone_id)

    def mark_up(self, drone_id: str) -> None:
        self._lost.discard(drone_id)
        self._back.discard(drone_id)


class LinkWatchdog:
    """LINK_LOST (m2-design section 8): every second, a watched drone silent for 5 s gets one
    derived WARNING alert and ``link=LOST``; its next heartbeat clears the alert and sets
    ``link=UP``.

    The shared Redis ``last_heartbeat_at`` is checked before raising, so a hot standby (which
    consumes nothing) never raises for drones the active ingestor still hears. The partial
    unique index on open derived alerts stops a second watchdog or a restart from raising a
    second one.
    """

    def __init__(
        self,
        sessions: sessionmaker[Session],
        state: StateWriter,
        detector: LinkDetector | None = None,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._state = state
        self.detector = detector or LinkDetector()
        self._wall_clock = wall_clock
        self._next_run = 0.0
        self._seeded = False

    def heard(self, drone_ids: Iterable[str], now: float) -> None:
        self.detector.heard(drone_ids, now)

    def maybe_run(self, now: float) -> None:
        try:
            if not self._seeded:
                self._seed(now)
            for drone_id in self.detector.returned():
                self._clear(drone_id)
            if now < self._next_run:
                return
            self._next_run = now + SWEEP_INTERVAL_S
            silent = self.detector.silent(now)
            if silent:
                self._raise_confirmed(silent, now)
        except (SQLAlchemyError, redis.RedisError, OSError) as exc:
            self._next_run = now + SWEEP_INTERVAL_S
            log.warning("link watchdog failed (%s), retrying", type(exc).__name__)

    def _seed(self, now: float) -> None:
        wall = self._wall_clock()
        with self._sessions.begin() as session:
            self.detector.seed_lost(store.open_link_lost(session))
        for drone_id, heard_at in self._state.last_heartbeats().items():
            self.detector.heard([drone_id], now - max(0.0, (wall - heard_at).total_seconds()))
        self._seeded = True

    def _raise_confirmed(self, silent: list[str], now: float) -> None:
        wall = self._wall_clock()
        shared = self._state.last_heartbeats(silent)
        for drone_id in silent:
            heard_at = shared.get(drone_id)
            age = (wall - heard_at).total_seconds() if heard_at else None
            if age is not None and age < self.detector.after_s:
                self.detector.heard([drone_id], now - max(0.0, age))
                continue
            with self._sessions.begin() as session:
                alert = store.raise_link_lost(session, drone_id, wall)
                events = [{"event": ALERT_RAISED, "data": alert_json(alert)}] if alert else []
            self.detector.mark_lost(drone_id)
            self._state.set_link(drone_id, "LOST")
            _publish(self._state, events, "link lost")
            log.warning("link lost", extra={"drone_id": drone_id, "raised": alert is not None})

    def _clear(self, drone_id: str) -> None:
        with self._sessions.begin() as session:
            cleared = store.clear_link_lost(session, drone_id, self._wall_clock())
            events = [{"event": ALERT_UPDATED, "data": alert_json(a)} for a in cleared]
        self.detector.mark_up(drone_id)
        self._state.set_link(drone_id, "UP")
        _publish(self._state, events, "link restored")
        log.info("link restored", extra={"drone_id": drone_id})


def _publish(state: StateWriter, events: list[dict[str, Any]], what: str) -> None:
    try:
        state.publish(events)
    except (redis.RedisError, OSError) as exc:
        # The rows are committed and the API reads them; only the push is lost.
        log.warning("%s events not published (%s)", what, type(exc).__name__)
