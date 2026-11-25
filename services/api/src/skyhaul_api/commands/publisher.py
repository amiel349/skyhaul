"""The ``CommandPublisher``: one thread per API process, the only code that touches AMQP.

Request threads hand it an encoded command and get a ``Future`` back. The thread publishes
to ``fleet.cmd`` with publisher confirms, ``mandatory=True`` and persistent delivery, and
always resolves the future with an ``Outcome``:

- ``CONFIRMED``: the broker confirmed it.
- ``NACKED``: the broker refused it ``NACK_ATTEMPTS`` times (same bounded retry as M1).
- ``UNROUTABLE``: no queue is bound for the routing key (returned by ``mandatory``).
- ``UNREACHABLE``: no connection could be opened or it broke.
- ``EXPIRED``: the deadline (5 s after submit) passed before a confirm; a message is never
  sent once its deadline has passed.
- ``STOPPED``: the publisher is shutting down.

The request thread therefore never decides about a publish whose outcome it does not know.
The queue is bounded (1,000): when it is full, ``submit`` raises ``PublisherFull``.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

import pika
from pika.exceptions import AMQPChannelError, AMQPConnectionError, NackError, UnroutableError
from pydantic import BaseModel
from skyhaul_contracts.codec import Codec
from skyhaul_contracts.message import Message
from skyhaul_contracts.topology import COMMAND_EXCHANGE

log = logging.getLogger(__name__)

QUEUE_SIZE: Final = 1000
DEADLINE_S: Final = 5.0
IDLE_S: Final = 0.5
RECONNECT_EVERY_S: Final = 2.0
NACK_ATTEMPTS: Final = 5
NACK_BACKOFF_S: Final = 0.05
CONNECT_TIMEOUT_S: Final = 2.0

type Connect = Callable[[], Any]
"""Opens a new ``pika.BlockingConnection`` (or a fake in tests)."""


class Outcome(StrEnum):
    CONFIRMED = "confirmed"
    NACKED = "nacked"
    UNROUTABLE = "unroutable"
    UNREACHABLE = "unreachable"
    EXPIRED = "expired"
    STOPPED = "stopped"


class PublisherFull(Exception):
    """The publisher queue is full."""


class PublisherThreadError(RuntimeError):
    """Something other than the publisher thread tried to publish."""


@dataclass(frozen=True)
class Outgoing:
    message: Message[BaseModel]
    body: bytes
    deadline: float
    future: Future[Outcome]


def broker_connect(
    host: str, port: int, vhost: str, user: str, password: str, *, deadline_s: float = DEADLINE_S
) -> Connect:
    params = pika.ConnectionParameters(
        host=host,
        port=port,
        virtual_host=vhost,
        credentials=pika.PlainCredentials(user, password),
        connection_attempts=1,
        socket_timeout=CONNECT_TIMEOUT_S,
        stack_timeout=CONNECT_TIMEOUT_S * 2,
        # A broker that blocks publishers (memory or disk alarm) must not pin the thread
        # past the deadline: pika closes the connection and the publish fails.
        blocked_connection_timeout=deadline_s,
        heartbeat=30,
    )
    return lambda: pika.BlockingConnection(params)


class CommandPublisher:
    def __init__(
        self,
        connect: Connect,
        *,
        queue_size: int = QUEUE_SIZE,
        deadline_s: float = DEADLINE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.deadline_s = deadline_s
        self._connect = connect
        self._clock = clock
        self._queue: queue.Queue[Outgoing | None] = queue.Queue(queue_size)
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._thread = threading.Thread(target=self._run, name="command-publisher", daemon=True)
        self._connection: Any = None
        self._channel: Any = None
        self._reconnect_at = 0.0

    # -- any thread --------------------------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop.set()
        # On a full queue the loop still sees the stop event within IDLE_S.
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(None)
        self._thread.join(timeout_s)

    @property
    def connected(self) -> bool:
        """True while the publisher's connection is open (the ``publisher`` readiness)."""
        return self._connected.is_set() and self._thread.is_alive()

    def submit(self, message: Message[BaseModel], body: bytes) -> Future[Outcome]:
        """Queue an encoded command. The future always resolves with an ``Outcome``."""
        future: Future[Outcome] = Future()
        if self._stop.is_set() or not self._thread.is_alive():
            future.set_result(Outcome.STOPPED)
            return future
        item = Outgoing(message, body, self._clock() + self.deadline_s, future)
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            raise PublisherFull from None
        return future

    # -- the publisher thread ----------------------------------------------------------

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    item = self._queue.get(timeout=IDLE_S)
                except queue.Empty:
                    self._idle()
                    continue
                if item is None:
                    break
                self.send(item)
        finally:
            self._resolve_rest()
            self._close()

    def send(self, item: Outgoing) -> None:
        """Publish one item and resolve its future, whatever happens."""
        if threading.current_thread() is not self._thread:
            raise PublisherThreadError("only the command publisher thread publishes")
        outcome = Outcome.UNREACHABLE
        try:
            outcome = self._publish(item)
        except Exception:  # never leave a future unresolved
            log.exception("command publish failed", extra=_extra(item))
            self._close()
        finally:
            item.future.set_result(outcome)
        if outcome is not Outcome.CONFIRMED:
            log.warning("command not sent: %s", outcome.value, extra=_extra(item))

    def _publish(self, item: Outgoing) -> Outcome:
        message = item.message
        routing_key = Codec.routing_key(message.drone_id, message.type.value)
        properties = Codec.amqp_properties(message, persistent=True)
        nacks = 0
        while True:
            if self._clock() >= item.deadline:
                return Outcome.EXPIRED
            channel = self._ensure_channel()
            if channel is None:
                return Outcome.UNREACHABLE
            try:
                channel.basic_publish(
                    COMMAND_EXCHANGE, routing_key, item.body, properties=properties, mandatory=True
                )
                return Outcome.CONFIRMED
            except UnroutableError:
                return Outcome.UNROUTABLE
            except NackError:
                nacks += 1
                if nacks >= NACK_ATTEMPTS:
                    return Outcome.NACKED
                backoff = min(NACK_BACKOFF_S * 2 ** (nacks - 1), 1.0)
                self._stop.wait(min(backoff, max(item.deadline - self._clock(), 0.0)))
            except (AMQPConnectionError, AMQPChannelError) as exc:
                # The same msg_id may be sent again; the drone dedups by msg_id (ADR 0006).
                log.warning("publisher connection lost (%s)", type(exc).__name__)
                self._close()
                self._reconnect_at = 0.0

    def _ensure_channel(self) -> Any:
        if self._channel is not None and self._channel.is_open:
            return self._channel
        self._close()
        try:
            connection = self._connect()
            channel = connection.channel()
            channel.confirm_delivery()
        except Exception as exc:  # any connect failure means "unreachable"
            log.warning("cannot reach RabbitMQ (%s)", type(exc).__name__)
            self._reconnect_at = self._clock() + RECONNECT_EVERY_S
            return None
        self._connection, self._channel = connection, channel
        self._connected.set()
        log.info("command publisher connected")
        return channel

    def _idle(self) -> None:
        """Service heartbeats, and reconnect now and then so readiness recovers."""
        if self._connection is not None and self._connection.is_open:
            try:
                self._connection.process_data_events(time_limit=0)
            except (AMQPConnectionError, AMQPChannelError) as exc:
                log.warning("publisher connection lost while idle (%s)", type(exc).__name__)
                self._close()
            return
        if self._clock() >= self._reconnect_at:
            self._ensure_channel()

    def _close(self) -> None:
        self._connected.clear()
        connection, self._connection, self._channel = self._connection, None, None
        if connection is not None and connection.is_open:
            try:
                connection.close()
            except Exception as exc:  # already broken; nothing left to release
                log.debug("closing the publisher connection failed (%s)", type(exc).__name__)

    def _resolve_rest(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            if item is not None and not item.future.done():
                item.future.set_result(Outcome.STOPPED)


def _extra(item: Outgoing) -> dict[str, str]:
    return {"correlation_id": str(item.message.correlation_id), "msg_id": item.message.msg_id}
