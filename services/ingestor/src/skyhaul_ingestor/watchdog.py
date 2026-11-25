"""Periodic jobs on the consumer thread.

- Telemetry retention (ADR 0012): hourly, rows with ``ts`` older than the retention are
  deleted in batches of 10,000, one batch per loop pass, so consumption keeps flowing while
  a backlog is cleared.
- The command sweeper (m2-design section 6): every second, SENT for longer than the command
  timeout (10 s) becomes TIMED_OUT and PENDING for longer than 30 s becomes FAILED. Each
  change is published as ``command.updated``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import redis
from skyhaul_fleet_core.commands import (
    COMMAND_TIMEOUT_S,
    COMMAND_UPDATED,
    PENDING_TIMEOUT_S,
    command_json,
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
    ) -> None:
        self._sessions = sessions
        self._state = state
        self._timeout = timedelta(seconds=timeout_s)
        self._pending_timeout = timedelta(seconds=PENDING_TIMEOUT_S)
        self._wall_clock = wall_clock
        self._next_run = 0.0

    def maybe_run(self, now: float) -> int:
        """Sweep if a run is due. Returns the number of commands moved."""
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
                events: list[dict[str, Any]] = [
                    {"event": COMMAND_UPDATED, "data": command_json(c)} for c in moved
                ]
        except SQLAlchemyError as exc:
            log.warning("command sweep failed (%s), retrying", type(exc).__name__)
            return 0
        try:
            self._state.publish(events)
        except (redis.RedisError, OSError) as exc:
            # The statuses are committed and the API reads them; only the push is lost.
            log.warning("command sweep events not published (%s)", type(exc).__name__)
        if moved:
            log.info("command sweep", extra={"moved": len(moved)})
        return len(moved)
