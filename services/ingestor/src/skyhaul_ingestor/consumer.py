"""The consumer loop. One thread owns everything: the batch, the registry cache, the jobs.

``process_data_events`` delivers messages to ``on_message`` (decode, classify, buffer);
between calls ``tick`` flushes a due batch and runs the periodic jobs.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pika
import pika.exceptions
import redis
from skyhaul_contracts.codec import Codec
from skyhaul_contracts.errors import DecodeError
from skyhaul_contracts.topology import (
    INGESTOR_QUEUE,
    declare_ingestor_queue,
    declare_topology,
)
from skyhaul_fleet_core.db import make_engine, make_session_factory
from sqlalchemy.exc import SQLAlchemyError

from skyhaul_ingestor.batch import Batch, Flusher, Pending
from skyhaul_ingestor.config import IngestorConfig
from skyhaul_ingestor.handlers import handle
from skyhaul_ingestor.registry import RegistryCache
from skyhaul_ingestor.state import StateWriter
from skyhaul_ingestor.watchdog import Retention

PREFETCH: Final = 1000
POLL_S: Final = 0.05
FLUSH_RETRY_S: Final = 1.0
STATS_EVERY_S: Final = 60.0
ALIVE_EVERY_S: Final = 1.0
REDIS_TIMEOUT_S: Final = 2

EXIT_OK: Final = 0
EXIT_BROKER: Final = 1
EXIT_NO_QUEUE: Final = 2

log = logging.getLogger(__name__)


class Ingestor:
    """The pika callbacks and the periodic work, without the connection (unit-testable)."""

    def __init__(
        self,
        flusher: Flusher,
        retention: Retention,
        counters: Counter[str],
        codec: Codec | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.batch = Batch()
        self.counters = counters
        self._flusher = flusher
        self._retention = retention
        self._codec = codec or Codec()
        self._clock = clock
        self._wall_clock = wall_clock
        self._retry_at = 0.0
        self._stats_at = clock() + STATS_EVERY_S

    def on_message(self, channel: Any, method: Any, _properties: Any, body: bytes) -> None:
        try:
            msg = self._codec.decode(body)
        except DecodeError as exc:
            channel.basic_reject(method.delivery_tag, requeue=False)
            self.counters["undecodable"] += 1
            log.warning("rejected undecodable message to the DLX", extra={"reason": exc.reason})
            return
        handled = handle(msg, self._wall_clock())
        self.batch.add(Pending(method.delivery_tag, msg.drone_id, handled), self._clock())

    def tick(self, channel: Any) -> None:
        now = self._clock()
        if self.batch.due(now) and now >= self._retry_at:
            self.flush(channel)
        self._retention.maybe_run(now)
        if now >= self._stats_at:
            self._stats_at = now + STATS_EVERY_S
            log.info("ingestor stats", extra={"counters": dict(self.counters)})

    def flush(self, channel: Any) -> bool:
        """Flush now. On a DB or Redis error keep the batch unacked and retry in 1 s."""
        if not self.batch.items:
            return True
        try:
            self._flusher.flush(self.batch, lambda tag: channel.basic_ack(tag, multiple=True))
        except (SQLAlchemyError, redis.RedisError, OSError) as exc:
            self.counters["flush_errors"] += 1
            self._retry_at = self._clock() + FLUSH_RETRY_S
            log.warning("flush failed, batch kept unacked (%s)", type(exc).__name__)
            return False
        return True


def build(config: IngestorConfig) -> Ingestor:
    sessions = make_session_factory(make_engine(config.database_url, pool_size=2))
    client = redis.Redis.from_url(
        config.redis_url, socket_timeout=REDIS_TIMEOUT_S, socket_connect_timeout=REDIS_TIMEOUT_S
    )
    counters: Counter[str] = Counter()
    flusher = Flusher(sessions, RegistryCache(), StateWriter(client), counters)
    return Ingestor(flusher, Retention(sessions, config.retention_days), counters)


def run(config: IngestorConfig, stop: threading.Event, ingestor: Ingestor | None = None) -> int:
    """Consume until ``stop`` is set. Exits 2 if the queue is missing, 1 if the broker is
    lost (unacked messages go back to the queue; the restart policy brings us back)."""
    ingestor = ingestor or build(config)
    params = pika.ConnectionParameters(
        host=config.rabbitmq_host,
        port=config.rabbitmq_port,
        virtual_host=config.rabbitmq_vhost,
        credentials=pika.PlainCredentials(config.rabbitmq_user, config.rabbitmq_password),
    )
    try:
        connection = pika.BlockingConnection(params)
    except pika.exceptions.AMQPError as exc:
        log.error("cannot connect to RabbitMQ (%s)", type(exc).__name__)
        return EXIT_BROKER
    try:
        channel = connection.channel()
        if config.declare_queue:
            declare_topology(channel)
            declare_ingestor_queue(channel)
        if not _queue_exists(channel):
            return EXIT_NO_QUEUE
        channel.basic_qos(prefetch_count=PREFETCH)
        channel.basic_consume(INGESTOR_QUEUE, ingestor.on_message, auto_ack=False)
        log.info("consuming telemetry", extra={"queue": INGESTOR_QUEUE})
        _loop(connection, channel, ingestor, stop, Path(config.alive_file))
        ingestor.flush(channel)
        return EXIT_OK
    except pika.exceptions.AMQPError as exc:
        log.error("lost the broker connection (%s)", type(exc).__name__)
        return EXIT_BROKER
    finally:
        if connection.is_open:
            connection.close()


def _queue_exists(channel: Any) -> bool:
    try:
        channel.queue_declare(INGESTOR_QUEUE, passive=True)
    except pika.exceptions.ChannelClosedByBroker as exc:
        if exc.reply_code != 404:
            raise
        log.error(
            "queue %s does not exist; broker-init must declare it (the ingestor declares nothing)",
            INGESTOR_QUEUE,
        )
        return False
    return True


def _loop(
    connection: Any, channel: Any, ingestor: Ingestor, stop: threading.Event, alive: Path
) -> None:
    alive_at = 0.0
    while not stop.is_set():
        connection.process_data_events(time_limit=POLL_S)
        ingestor.tick(channel)
        now = time.monotonic()
        if now >= alive_at:
            alive_at = now + ALIVE_EVERY_S
            alive.touch()
