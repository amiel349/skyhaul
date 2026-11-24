"""The publisher thread: the only code that sends, on its own connection.

It takes messages from the outbox in FIFO order, encodes them through the Codec, and
publishes each one with publisher confirms (`basic_publish` returns once the broker has
confirmed it). A lost connection is reopened and the same message is sent again, so
nothing waiting in the outbox is lost. While idle it services the connection every 0.5 s,
so broker heartbeats never expire.

Shutdown: once the stop event is set it keeps draining up to the sentinel for at most
`drain_s`; whatever is left after that is counted and logged as dropped.
"""

import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any, Final

from pika.exceptions import AMQPChannelError, AMQPConnectionError, NackError
from skyhaul_contracts import Codec, EncodeError
from skyhaul_contracts.topology import exchange_for

from skyhaul_drone_sim.amqp import BrokerUnavailable, Connect, close_quietly
from skyhaul_drone_sim.outbox import SENTINEL, Outbox, Outgoing

log = logging.getLogger(__name__)

IDLE_S: Final = 0.5
"""How long `outbox.get` waits before servicing the connection (heartbeats)."""

NACK_ATTEMPTS: Final = 5
"""How many times one message is offered to a broker that nacks it before it is dropped.
Without a cap, one message the broker keeps refusing would pin this thread, fill the
outbox and stop the consumer."""

NACK_BACKOFF_S: Final = 0.05
"""First wait between attempts after a nack; doubles each time, capped at 1 s."""


class PublisherThreadError(RuntimeError):
    """Something other than the publisher thread tried to publish."""


class Publisher:
    def __init__(
        self,
        *,
        codec: Codec,
        outbox: Outbox,
        stop: threading.Event,
        connect: Connect,
        connect_timeout_s: float,
        drain_s: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._codec = codec
        self._outbox = outbox
        self._stop = stop
        self._connect = connect
        self._connect_timeout_s = connect_timeout_s
        self._drain_s = drain_s
        self._monotonic = monotonic
        # Daemon, so a publish stuck on a blocked broker cannot keep the process past the
        # shutdown bound; the main thread joins it with a timeout.
        self._thread = threading.Thread(target=self._run, name="publisher", daemon=True)
        self._connection: Any = None
        self._channel: Any = None
        self._deadline: float | None = None
        self.ready = threading.Event()
        """Set once the first connection is open, or once it has failed (see `failed`)."""
        self.failed = False
        self.published = 0
        self.dropped = 0

    # -- lifecycle (called from the main thread) ---------------------------------------

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def begin_drain(self) -> None:
        """Start the shutdown drain clock now. Called right after the stop event is set, so
        a publish that is stuck at that moment cannot push the bound back. (A single float
        assignment; the publisher thread only reads it.)"""
        if self._deadline is None:
            self._deadline = self._monotonic() + self._drain_s

    # -- the publisher thread ----------------------------------------------------------

    def _run(self) -> None:
        try:
            self._open(self._connect_timeout_s, self._stop)
        except BrokerUnavailable as exc:
            log.error("publisher could not connect", extra={"error": str(exc)})
            self.failed = True
            self.ready.set()
            return
        self.ready.set()
        try:
            self._loop()
        finally:
            close_quietly(self._connection)
            log.info(
                "publisher stopped",
                extra={"published": self.published, "dropped": self.dropped},
            )

    def _loop(self) -> None:
        while not self._past_deadline():
            try:
                item = self._outbox.get(timeout=IDLE_S)
            except queue.Empty:
                self._keep_alive()
                continue
            if item is SENTINEL:
                return
            self.send(item)
        self._drop_rest()

    def send(self, out: Outgoing) -> None:
        """Encode and publish one message, reconnecting until it is confirmed (or the
        shutdown deadline passes). Only the publisher thread may call it."""
        if threading.current_thread() is not self._thread:
            raise PublisherThreadError("only the publisher thread publishes")
        message = out.message
        try:
            body = self._codec.encode(message)
        except EncodeError as exc:  # a simulator bug; the codec counts it
            log.error(
                "message not encodable, dropped",
                extra={"type": exc.type, "errors": exc.errors, "msg_id": message.msg_id},
            )
            self.dropped += 1
            return
        exchange = exchange_for(message.type)
        routing_key = Codec.routing_key(message.drone_id, message.type.value)
        properties = Codec.amqp_properties(message, persistent=out.persistent)
        nacks = 0
        while True:
            try:
                # mandatory=False: telemetry nobody subscribes to is fine to drop.
                self._channel.basic_publish(
                    exchange, routing_key, body, properties=properties, mandatory=False
                )
                self.published += 1
                return
            except NackError:
                nacks += 1
                if nacks >= NACK_ATTEMPTS or self._past_deadline():
                    log.error(
                        "broker kept refusing a message, dropped",
                        extra={"msg_id": message.msg_id, "type": message.type.value},
                    )
                    self.dropped += 1
                    return
                log.warning("broker refused a message, sending it again")
                # Back off; the stop event ends the wait early so shutdown is not delayed.
                self._stop.wait(min(NACK_BACKOFF_S * 2 ** (nacks - 1), 1.0))
            except (AMQPConnectionError, AMQPChannelError) as exc:
                log.warning(
                    "publisher connection lost, reconnecting",
                    extra={"error": type(exc).__name__},
                )
                if not self._reopen():
                    self.dropped += 1
                    return

    def _keep_alive(self) -> None:
        try:
            self._connection.process_data_events(time_limit=0)
        except (AMQPConnectionError, AMQPChannelError) as exc:
            log.warning(
                "publisher connection lost while idle, reconnecting",
                extra={"error": type(exc).__name__},
            )
            self._reopen()

    def _open(self, timeout_s: float, stop: threading.Event | None) -> None:
        connection = self._connect(timeout_s, stop)
        try:
            channel = connection.channel()
            channel.confirm_delivery()
        except Exception:
            close_quietly(connection)
            raise
        self._connection, self._channel = connection, channel

    def _reopen(self) -> bool:
        """Reconnect. Keeps trying while running; gives up at the shutdown deadline."""
        close_quietly(self._connection)
        while True:
            deadline = self._drain_deadline()
            timeout_s = self._connect_timeout_s
            if deadline is not None:
                timeout_s = deadline - self._monotonic()
                if timeout_s <= 0:
                    return False
            try:
                self._open(timeout_s, None)
                log.info("publisher reconnected")
                return True
            except (BrokerUnavailable, AMQPConnectionError, AMQPChannelError):
                log.warning("publisher still cannot reach RabbitMQ")

    def _drain_deadline(self) -> float | None:
        if self._deadline is None and self._stop.is_set():
            self._deadline = self._monotonic() + self._drain_s
        return self._deadline

    def _past_deadline(self) -> bool:
        deadline = self._drain_deadline()
        return deadline is not None and self._monotonic() >= deadline

    def _drop_rest(self) -> None:
        dropped = 0
        while True:
            try:
                item = self._outbox.get_nowait()
            except queue.Empty:
                break
            if item is not SENTINEL:
                dropped += 1
        self.dropped += dropped
        log.error(
            "shutdown drain deadline passed, messages dropped",
            extra={"dropped": dropped, "drain_s": self._drain_s},
        )
