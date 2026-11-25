"""Unit tests of the ingestor: flush rules and order, dedup, registry TTLs, the Codec gate."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from skyhaul_contracts.codec import Codec
from skyhaul_contracts.types import MessageType
from skyhaul_contracts.v1 import AckPayload, BatteryPayload, HeartbeatPayload
from skyhaul_ingestor import batch as batch_module
from skyhaul_ingestor import store
from skyhaul_ingestor.batch import FLUSH_MAX_MESSAGES, Batch, Flusher, Pending
from skyhaul_ingestor.consumer import Ingestor
from skyhaul_ingestor.handlers import Handled, StateUpdate, handle
from skyhaul_ingestor.registry import RegistryCache
from sqlalchemy.exc import OperationalError

NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)
codec = Codec()


def battery(drone_id: str = "u-d001") -> Any:
    return codec.new(
        MessageType.BATTERY, drone_id, BatteryPayload(charge_pct=80, est_remaining_s=600), None
    )


def heartbeat(drone_id: str = "u-d001") -> Any:
    payload = HeartbeatPayload(flight_state="LANDED", armed=False, uptime_s=3)
    return codec.new(MessageType.HEARTBEAT, drone_id, payload, None)


class FakeSessions:
    """``sessionmaker.begin()`` stand-in that records events in a shared log."""

    def __init__(self, events: list[str], fail: bool = False) -> None:
        self.events, self.fail = events, fail

    @contextmanager
    def begin(self) -> Iterator[object]:
        if self.fail:
            raise OperationalError("connect", {}, Exception("down"))
        yield object()
        self.events.append("commit")


@dataclass
class FakeState:
    events: list[str]
    updates: list[tuple[str, StateUpdate]] | None = None

    def write(self, updates: list[tuple[str, StateUpdate]], _seen: datetime) -> int:
        self.events.append("redis")
        self.updates = list(updates)
        return 0


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fake store: drones listed in ``registered``; inserted rows recorded."""
    fake: dict[str, Any] = {"registered": {"u-d001"}, "rows": []}
    monkeypatch.setattr(store, "registered_ids", lambda _s, ids: ids & fake["registered"])

    def insert(_session: object, rows: list[dict[str, Any]]) -> int:
        fake["rows"].extend(rows)
        return len(rows)

    monkeypatch.setattr(store, "insert_telemetry", insert)
    return fake


def flusher(events: list[str], *, fail_db: bool = False) -> tuple[Flusher, FakeState, Counter[str]]:
    state, counters = FakeState(events), Counter[str]()
    sessions: Any = FakeSessions(events, fail=fail_db)
    return Flusher(sessions, RegistryCache(), state, counters), state, counters  # type: ignore[arg-type]


def pending(tag: int, msg: Any) -> Pending:
    return Pending(tag, msg.drone_id, handle(msg, NOW))


def test_batch_is_due_after_200_ms_or_500_messages() -> None:
    batch = Batch()
    assert not batch.due(0.0)
    batch.add(pending(1, battery()), now=10.0)
    assert not batch.due(10.19)
    assert batch.due(10.21)
    full = Batch()
    for tag in range(1, FLUSH_MAX_MESSAGES + 1):
        full.add(pending(tag, battery()), now=10.0)
    assert full.due(10.0)


def test_flush_commits_then_writes_redis_then_acks_once(db: dict[str, Any]) -> None:
    events: list[str] = []
    flush, _, counters = flusher(events)
    batch = Batch()
    for tag in (1, 2, 3):
        batch.add(pending(tag, battery()), now=0.0)
    flush.flush(batch, lambda tag: events.append(f"ack {tag}"))
    assert events == ["commit", "redis", "ack 3"]
    assert (len(db["rows"]), counters["stored"], batch.items) == (3, 3, [])


def test_db_failure_acks_nothing_and_keeps_the_batch(db: dict[str, Any]) -> None:
    events: list[str] = []
    flush, _, _ = flusher(events, fail_db=True)
    batch = Batch()
    batch.add(pending(1, battery()), now=0.0)
    with pytest.raises(OperationalError):
        flush.flush(batch, lambda tag: events.append(f"ack {tag}"))
    assert events == []
    assert len(batch.items) == 1


def test_same_msg_id_twice_in_a_batch_is_one_row(db: dict[str, Any]) -> None:
    flush, _, _ = flusher([])
    msg, batch = battery(), Batch()
    batch.add(pending(1, msg), now=0.0)
    batch.add(pending(2, msg), now=0.0)
    flush.flush(batch, lambda _tag: None)
    assert len(db["rows"]) == 1


def test_unregistered_drone_is_acked_and_counted_with_no_row_and_no_state(
    db: dict[str, Any],
) -> None:
    events: list[str] = []
    flush, state, counters = flusher(events)
    batch = Batch()
    batch.add(pending(1, battery("stranger-d001")), now=0.0)
    batch.add(pending(2, heartbeat("stranger-d001")), now=0.0)
    flush.flush(batch, lambda tag: events.append(f"ack {tag}"))
    assert (db["rows"], state.updates) == ([], [])
    assert events[-1] == "ack 2"
    assert counters["unregistered_drone_messages"] == 2


def test_heartbeat_updates_state_but_is_not_stored() -> None:
    handled = handle(heartbeat(), NOW)
    assert handled.row is None
    assert handled.state is not None
    assert handled.state.fields["flight_state"] == "LANDED"
    stored = handle(battery(), NOW)
    assert stored.row is not None
    assert stored.row["type"] == "BATTERY"


def test_registry_cache_keeps_positives_30_s_and_negatives_5_s() -> None:
    now = [0.0]
    cache = RegistryCache(clock=lambda: now[0])
    loads: list[set[str]] = []

    def load(ids: set[str]) -> set[str]:
        loads.append(ids)
        return ids & {"yes-d001"}

    assert cache.registered({"yes-d001", "no-d001"}, load) == {"yes-d001"}
    now[0] = 4.9
    cache.registered({"yes-d001", "no-d001"}, load)
    now[0] = 5.0
    cache.registered({"yes-d001", "no-d001"}, load)
    now[0] = 30.0
    cache.registered({"yes-d001"}, load)
    assert loads == [{"yes-d001", "no-d001"}, {"no-d001"}, {"yes-d001"}]


@dataclass
class Method:
    delivery_tag: int


class FakeChannel:
    def __init__(self) -> None:
        self.rejected: list[tuple[int, bool]] = []

    def basic_reject(self, tag: int, requeue: bool) -> None:
        self.rejected.append((tag, requeue))


def test_undecodable_message_is_rejected_without_requeue_and_good_ones_keep_flowing() -> None:
    counters: Counter[str] = Counter()
    ingestor = Ingestor(flusher=None, retention=None, counters=counters)  # type: ignore[arg-type]
    channel = FakeChannel()
    ingestor.on_message(channel, Method(1), None, b"{not json")
    ingestor.on_message(channel, Method(2), None, codec.encode(battery()))
    assert channel.rejected == [(1, False)]
    assert counters["undecodable"] == 1
    assert [item.tag for item in ingestor.batch.items] == [2]


def test_retention_batch_size_is_10000() -> None:
    assert store.RETENTION_BATCH == 10_000
    assert batch_module.FLUSH_INTERVAL_S == 0.2


def test_ack_and_nack_are_not_stored() -> None:
    payload = AckPayload(command_msg_id=str(uuid.uuid4()), command_type="ARM")
    ack = codec.new(MessageType.ACK, "u-d001", payload, str(uuid.uuid4()))
    assert handle(ack, NOW) == Handled(None, None)
