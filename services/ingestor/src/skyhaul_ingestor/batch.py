"""The batch buffer and its flush: one DB transaction, then Redis, then ``basic_ack``.

Nothing is acked before the commit. A failed flush keeps the batch for the next attempt;
the insert skips stored msg_ids and the Redis writes are idempotent, so a retry is safe.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from sqlalchemy.orm import Session, sessionmaker

from skyhaul_ingestor import store
from skyhaul_ingestor.handlers import Handled
from skyhaul_ingestor.registry import RegistryCache
from skyhaul_ingestor.state import StateWriter

FLUSH_INTERVAL_S: Final = 0.2
FLUSH_MAX_MESSAGES: Final = 500

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Pending:
    tag: int
    drone_id: str
    handled: Handled


@dataclass
class Batch:
    items: list[Pending] = field(default_factory=list)
    started_at: float = 0.0

    def add(self, item: Pending, now: float) -> None:
        if not self.items:
            self.started_at = now
        self.items.append(item)

    def due(self, now: float) -> bool:
        if not self.items:
            return False
        full = len(self.items) >= FLUSH_MAX_MESSAGES
        return full or now - self.started_at >= FLUSH_INTERVAL_S


class Flusher:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        registry: RegistryCache,
        state: StateWriter,
        counters: Counter[str],
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._registry = registry
        self._state = state
        self._counters = counters
        self._wall_clock = wall_clock

    def flush(self, batch: Batch, ack: Callable[[int], None]) -> None:
        """Write and ack ``batch``, then empty it. Raises (and keeps it) if a step fails."""
        drone_ids = {item.drone_id for item in batch.items}
        with self._sessions.begin() as session:
            known = self._registry.registered(
                drone_ids, lambda misses: store.registered_ids(session, misses)
            )
            rows = {
                row["msg_id"]: row
                for item in batch.items
                if item.drone_id in known and (row := item.handled.row) is not None
            }
            inserted = store.insert_telemetry(session, list(rows.values()))
        updates = [
            (item.drone_id, state)
            for item in batch.items
            if item.drone_id in known and (state := item.handled.state) is not None
        ]
        self._state.write(updates, self._wall_clock())
        ack(batch.items[-1].tag)
        unregistered = sum(1 for item in batch.items if item.drone_id not in known)
        self._counters.update(
            acked=len(batch.items),
            stored=inserted,
            duplicates=len(rows) - inserted,
            unregistered_drone_messages=unregistered,
        )
        batch.items.clear()
