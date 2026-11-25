"""The batch buffer and its flush: one DB transaction, then Redis, then ``basic_ack``.

The transaction stores telemetry, applies ACK and NACK replies to their commands and
MISSION_STATUS to missions (forward-only CAS updates, in arrival order), and inserts drone
alerts once per msg_id. Redis then gets the latest state and the ``command.updated``,
``mission.updated`` and ``alert.raised`` events.
Nothing is acked before the commit. A failed flush keeps the batch for the next attempt;
the insert skips stored msg_ids and the Redis writes are idempotent, so a retry is safe.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from skyhaul_fleet_core.commands import COMMAND_UPDATED, command_json
from skyhaul_fleet_core.missions import ALERT_RAISED, MISSION_UPDATED, alert_json, mission_json
from sqlalchemy.orm import Session, sessionmaker

from skyhaul_ingestor import store
from skyhaul_ingestor.handlers import Handled, MissionUpdate, Reply
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
    # Events of committed changes not yet published; kept if the Redis step fails, because
    # the retried transaction finds the commands already moved and returns none again.
    events: list[dict[str, Any]] = field(default_factory=list)

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
        on_heartbeats: Callable[[set[str]], None] | None = None,
    ) -> None:
        self._sessions = sessions
        self._registry = registry
        self._state = state
        self._counters = counters
        self._wall_clock = wall_clock
        # Registered drones that sent a HEARTBEAT in the flushed batch (the link watchdog).
        self._on_heartbeats = on_heartbeats

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
            applied = Counter[str]()
            events = self._apply(session, batch, known, applied)
        batch.events.extend(events)  # only once committed
        updates = [
            (item.drone_id, state)
            for item in batch.items
            if item.drone_id in known and (state := item.handled.state) is not None
        ]
        self._state.write(updates, self._wall_clock())
        self._state.publish(batch.events)
        batch.events.clear()
        ack(batch.items[-1].tag)
        if self._on_heartbeats is not None:
            heard = {i.drone_id for i in batch.items if i.handled.heartbeat and i.drone_id in known}
            self._on_heartbeats(heard)
        unregistered = sum(1 for item in batch.items if item.drone_id not in known)
        replies = sum(1 for item in batch.items if item.handled.reply is not None)
        self._counters.update(
            replies_applied=applied["replies"],
            replies_ignored=replies - applied["replies"],
            mission_status_ignored=applied["mission_status_ignored"],
            alerts_raised=applied["alerts"],
            acked=len(batch.items),
            stored=inserted,
            duplicates=len(rows) - inserted,
            unregistered_drone_messages=unregistered,
        )
        batch.items.clear()

    def _apply(
        self, session: Session, batch: Batch, known: set[str], applied: Counter[str]
    ) -> list[dict[str, Any]]:
        """Apply replies, mission statuses and alerts in arrival order, with one event per
        change. Unknown, duplicate, mismatched or out-of-order messages change nothing."""
        events: list[dict[str, Any]] = []
        for item in batch.items:
            if item.drone_id not in known:
                continue
            handled = item.handled
            if handled.reply is not None:
                events += self._apply_reply(session, handled.reply, applied)
            if handled.mission is not None:
                events += self._apply_mission(session, handled.mission, applied)
        alerts = store.insert_alerts(
            session,
            [alert for i in batch.items if i.drone_id in known and (alert := i.handled.alert)],
        )
        applied["alerts"] += len(alerts)
        events += [{"event": ALERT_RAISED, "data": alert_json(a)} for a in alerts]
        return events

    def _apply_reply(
        self, session: Session, reply: Reply, applied: Counter[str]
    ) -> list[dict[str, Any]]:
        command = store.apply_reply(session, reply)
        if command is None:
            log.info("reply ignored", extra={"command_msg_id": str(reply.command_id)})
            return []
        applied["replies"] += 1
        events = [{"event": COMMAND_UPDATED, "data": command_json(command)}]
        mission = store.apply_start_reply(session, command)
        if mission is not None:
            events.append({"event": MISSION_UPDATED, "data": mission_json(mission)})
        return events

    def _apply_mission(
        self, session: Session, status: MissionUpdate, applied: Counter[str]
    ) -> list[dict[str, Any]]:
        mission_id = status.mission_id
        mission = None if mission_id is None else store.apply_mission_status(session, status)
        if mission is not None:
            return [{"event": MISSION_UPDATED, "data": mission_json(mission)}]
        if mission_id is None or not store.mission_of_drone(session, mission_id, status.drone_id):
            applied["mission_status_ignored"] += 1
            log.warning(
                "mission status for an unknown mission or another drone ignored",
                extra={"mission_id": str(status.mission_id), "drone_id": status.drone_id},
            )
        return []
