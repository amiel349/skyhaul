"""Periodic jobs on the consumer thread. In #27: telemetry retention (ADR 0012).

Hourly, rows with ``ts`` older than the retention are deleted in batches of 10,000, one
batch per loop pass, so consumption keeps flowing while a backlog is cleared.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from skyhaul_ingestor import store

RETENTION_INTERVAL_S: Final = 3600.0
RETRY_AFTER_ERROR_S: Final = 60.0

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
