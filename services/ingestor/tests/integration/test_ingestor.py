"""The ingestor against real Postgres, Redis and RabbitMQ (#27), the simulator as a source.

Deferred tests (light test mode; #19 owns the load ones):
- Throughput at 200 drones, prefetch tuning, poison-message flood, restart mid-batch under
  load, Redis down while Postgres is up (#19).
- A real SIGTERM to a subprocess ingestor (last flush, exit 0) and the broker-loss exit 1.
- MISSION_STATUS and ALERT emitted by the simulator itself (here they are published by the
  test through the Codec); the API E2E suite covers the simulator flow end to end.
- The registry cache seeing a soft delete only after its 30 s TTL.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pika
import pytest
import redis
from pydantic import BaseModel
from sim_support.command_client import CommandClient
from skyhaul_contracts.codec import Codec
from skyhaul_contracts.message import Message
from skyhaul_contracts.topology import DEAD_LETTER_QUEUE
from skyhaul_contracts.types import MessageType
from skyhaul_contracts.v1 import (
    AlertPayload,
    ArmPayload,
    BatteryPayload,
    MissionStatusPayload,
    PositionPayload,
    TakeoffPayload,
)
from skyhaul_fleet_core.db import make_engine, make_session_factory
from skyhaul_fleet_core.keys import EVENTS_CHANNEL
from skyhaul_fleet_core.models import Telemetry
from skyhaul_ingestor import consumer
from skyhaul_ingestor.batch import Flusher
from skyhaul_ingestor.consumer import EXIT_NO_QUEUE
from skyhaul_ingestor.handlers import StateUpdate
from skyhaul_ingestor.state import StateWriter
from skyhaul_ingestor.watchdog import Retention
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from .conftest import Stack, StartIngestor, wait_until

pytestmark = pytest.mark.integration

codec = Codec()
type Publish = Callable[[Message[BaseModel] | bytes, str], None]


def rows(engine: Engine, drone_id: str, type: str | None = None) -> list[Telemetry]:
    statement = select(Telemetry).where(Telemetry.drone_id == drone_id)
    if type is not None:
        statement = statement.where(Telemetry.type == type)
    with Session(engine) as session:
        return list(session.scalars(statement))


def battery(drone_id: str, pct: float = 80.0, ts: datetime | None = None) -> Message[BaseModel]:
    payload = BatteryPayload(charge_pct=pct, est_remaining_s=600)
    return codec.new(MessageType.BATTERY, drone_id, payload, None, ts=ts)


def position(drone_id: str, lat: float, ts: datetime) -> Message[BaseModel]:
    payload = PositionPayload(
        lat=lat, lon=34.8, altitude_m=20, heading_deg=0, ground_speed_mps=0, target=None
    )
    return codec.new(MessageType.POSITION, drone_id, payload, str(uuid.uuid4()), ts=ts)


def test_missing_queue_makes_the_ingestor_exit_with_a_clear_error_and_declare_nothing(
    stack: Stack,
    start_ingestor: StartIngestor,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(consumer, "INGESTOR_QUEUE", "ingestor.missing")
    assert start_ingestor().close() == EXIT_NO_QUEUE
    assert "ingestor.missing does not exist" in caplog.text
    connection = pika.BlockingConnection(stack.amqp)
    with pytest.raises(pika.exceptions.ChannelClosedByBroker):
        connection.channel().queue_declare("ingestor.missing", passive=True)
    connection.close()


def test_simulated_drone_telemetry_lands_in_postgres_and_redis_on_one_thread(
    stack: Stack,
    engine: Engine,
    redis_client: redis.Redis,
    register: Callable[[str], None],
    drone_id: str,
    start_ingestor: StartIngestor,
    start_sim: Callable[[list[str]], None],
) -> None:
    stranger = drone_id.replace("-d001", "-d002")
    register(drone_id)
    events = redis_client.pubsub()
    events.subscribe(EVENTS_CHANNEL)
    baseline = threading.active_count()
    running = start_ingestor()
    start_sim([drone_id, stranger])
    with CommandClient(stack.amqp) as client:
        client.watch(drone_id)
        client.wait_reply(client.send(MessageType.ARM, drone_id, ArmPayload()).msg_id, 30)
        takeoff = client.send(MessageType.TAKEOFF, drone_id, TakeoffPayload(altitude_m=20.0))
        client.wait_reply(takeoff.msg_id)

    stored = wait_until(lambda: rows(engine, drone_id, "POSITION"), what="POSITION rows")
    wait_until(lambda: rows(engine, drone_id, "BATTERY"), timeout_s=15, what="BATTERY rows")
    state = wait_until(
        lambda: (s := redis_client.hgetall(f"drone:{drone_id}")) and "battery_pct" in s and s,
        what="latest state",
    )
    assert threading.active_count() == baseline + 1  # the ingestor thread, nothing else
    row = stored[0]
    assert row.received_at >= row.ts - timedelta(seconds=5)
    assert set(row.payload) >= {"lat", "lon", "altitude_m"}
    assert {r.type for r in rows(engine, drone_id)} <= {"POSITION", "BATTERY"}  # no HEARTBEAT
    assert {"flight_state", "lat", "last_heartbeat_at", "link"} <= set(state)
    assert json.loads(state["link"]) == "UP"
    assert rows(engine, stranger) == []
    assert not redis_client.exists(f"drone:{stranger}")
    assert running.counters["unregistered_drone_messages"] > 0
    pushed = [
        m for m in iter(lambda: events.get_message(timeout=0.5), None) if m["type"] == "message"
    ]
    ours = [json.loads(m["data"]) for m in pushed if drone_id in m["data"]]
    assert ours
    assert ours[0]["event"] == "drone.state"
    assert set(ours[0]["data"]) == {"drone_id", "state"}
    running.close()


def test_mission_status_and_alert_are_stored_once_with_their_envelope(
    engine: Engine,
    register: Callable[[str], None],
    drone_id: str,
    start_ingestor: StartIngestor,
    publish: Publish,
) -> None:
    register(drone_id)
    running = start_ingestor()
    status = codec.new(
        MessageType.MISSION_STATUS,
        drone_id,
        MissionStatusPayload(
            mission_id="m1", state="ACTIVE", waypoint_index=0, waypoints_total=2, progress_pct=0
        ),
        str(uuid.uuid4()),
    )
    alert = codec.new(
        MessageType.ALERT,
        drone_id,
        AlertPayload(code="LOW_BATTERY", severity="WARNING", message="<b>low</b>", value=19.0),
        None,
    )
    publish(status, "")
    publish(status, "")  # a redelivery: same msg_id
    publish(alert, "")
    wait_until(lambda: len(rows(engine, drone_id)) == 2 and running.counters["acked"] >= 3)

    by_type = {r.type: r for r in rows(engine, drone_id)}
    assert str(by_type["MISSION_STATUS"].msg_id) == status.msg_id
    assert str(by_type["MISSION_STATUS"].correlation_id) == status.correlation_id
    assert by_type["MISSION_STATUS"].ts.isoformat().startswith(status.ts[:19])
    assert by_type["ALERT"].payload["message"] == "<b>low</b>"  # plain data, unchanged
    assert running.counters["duplicates"] >= 1
    running.close()


def test_older_ts_never_overwrites_newer_state_and_one_event_per_drone_per_flush(
    redis_client: redis.Redis, drone_id: str, stack: Stack
) -> None:
    writer = StateWriter(redis.Redis.from_url(stack.redis_url))
    now = datetime.now(UTC)
    events = redis_client.pubsub()
    events.subscribe(EVENTS_CHANNEL)
    events.get_message(timeout=1)  # the subscribe confirmation

    def pos(lat: float, ts: datetime) -> tuple[str, StateUpdate]:
        msg = position(drone_id, lat, ts)
        return drone_id, StateUpdate("pos", msg.ts, msg.payload.model_dump(mode="json"))

    sent = writer.write([pos(1.0, now), pos(2.0, now - timedelta(seconds=1)), pos(3.0, now)], now)

    assert sent == 1
    assert json.loads(redis_client.hget(f"drone:{drone_id}", "lat") or "null") == 1.0
    message = events.get_message(timeout=2)
    assert message is not None
    assert json.loads(message["data"])["data"]["state"]["position"]["lat"] == 1.0
    assert events.get_message(timeout=0.5) is None
    assert writer.write([pos(0.5, now - timedelta(seconds=5))], now) == 0


def test_undecodable_message_goes_to_the_dlx_and_good_ones_keep_flowing(
    stack: Stack,
    engine: Engine,
    register: Callable[[str], None],
    drone_id: str,
    start_ingestor: StartIngestor,
    publish: Publish,
) -> None:
    register(drone_id)
    running = start_ingestor()
    garbage = f"not json {uuid.uuid4()}".encode()
    publish(garbage, f"drone.{drone_id}.POSITION")
    publish(battery(drone_id), "")
    wait_until(lambda: rows(engine, drone_id, "BATTERY"), what="the good message")
    running.close()

    connection = pika.BlockingConnection(stack.amqp)
    channel = connection.channel()
    dead = []
    while (got := channel.basic_get(DEAD_LETTER_QUEUE, auto_ack=True))[0] is not None:
        dead.append(got[2])
    connection.close()
    assert garbage in dead
    assert running.counters["undecodable"] == 1


class Crash(Exception):
    """Stands in for the process dying after the commit, before the ack."""


def test_crash_between_commit_and_ack_redelivers_without_a_duplicate_row(
    stack: Stack,
    engine: Engine,
    register: Callable[[str], None],
    drone_id: str,
    start_ingestor: StartIngestor,
    publish: Publish,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register(drone_id)
    real_flush = Flusher.flush

    def crash(_tag: int) -> None:
        raise Crash

    def flush_then_crash(self: Flusher, batch: Any, _ack: Callable[[int], None]) -> None:
        real_flush(self, batch, crash)

    monkeypatch.setattr(Flusher, "flush", flush_then_crash)
    first = start_ingestor()
    for pct in (90.0, 80.0, 70.0):
        publish(battery(drone_id, pct), "")
    wait_until(lambda: first.errors, what="the injected crash")
    first.thread.join(5)
    monkeypatch.undo()
    assert len(rows(engine, drone_id)) == 3  # committed, never acked

    second = start_ingestor()
    wait_until(lambda: second.counters["duplicates"] >= 3, what="the redelivery")
    assert len(rows(engine, drone_id)) == 3
    second.close()


def test_postgres_outage_acks_nothing_and_recovery_writes_everything_once(
    engine: Engine,
    register: Callable[[str], None],
    drone_id: str,
    start_ingestor: StartIngestor,
    publish: Publish,
) -> None:
    register(drone_id)
    running = start_ingestor()
    with engine.begin() as conn:
        conn.execute(text("ALTER ROLE ingestor NOLOGIN"))
        conn.execute(
            text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = :u"),
            {"u": "ingestor"},
        )
    try:
        for pct in (90.0, 80.0, 70.0, 60.0):
            publish(battery(drone_id, pct), "")

        def held() -> int:
            # In the unacked batch: a failed flush raises before its ack and keeps it.
            return sum(1 for i in list(running.ingestor.batch.items) if i.drone_id == drone_id)

        wait_until(lambda: held() == 4, what="the messages in the batch")
        errors = running.counters["flush_errors"]
        wait_until(lambda: running.counters["flush_errors"] > errors, what="a failed flush")
        assert held() == 4
        assert rows(engine, drone_id) == []
    finally:
        with engine.begin() as conn:
            conn.execute(text("ALTER ROLE ingestor LOGIN"))
    wait_until(lambda: len(rows(engine, drone_id)) == 4, what="the writes after recovery")
    wait_until(lambda: not running.ingestor.batch.items, what="the ack")
    running.close()
    assert len(rows(engine, drone_id)) == 4


def test_retention_deletes_rows_older_than_7_days_in_batches_and_keeps_newer(
    stack: Stack, engine: Engine, drone_id: str
) -> None:
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        for age in (8, 8, 8, 9, 30, 6, 0):
            msg = battery(drone_id, ts=now - timedelta(days=age))
            session.add(
                Telemetry(
                    msg_id=uuid.UUID(msg.msg_id),
                    drone_id=drone_id,
                    type="BATTERY",
                    ts=now - timedelta(days=age),
                    correlation_id=None,
                    payload={},
                )
            )
    sessions = make_session_factory(make_engine(stack.ingestor_url, pool_size=1))
    retention = Retention(sessions, days=7, batch_size=2)

    passes = [retention.maybe_run(now=0.0) for _ in range(3)]
    later = retention.maybe_run(now=1.0)

    assert passes == [2, 2, 1]
    assert later == 0  # the next run is an hour away
    with Session(engine) as session:
        left = session.scalar(
            select(func.count()).select_from(Telemetry).where(Telemetry.drone_id == drone_id)
        )
    assert left == 2
