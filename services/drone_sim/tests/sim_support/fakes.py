"""In-memory stand-ins for pika's BlockingConnection and BlockingChannel.

They record which thread called what, so tests can prove that only the publisher thread
publishes and that a command is acked only after its replies are in the outbox.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any


@dataclass
class Published:
    thread: threading.Thread
    exchange: str
    routing_key: str
    body: bytes
    properties: Any
    mandatory: bool


@dataclass
class FakeChannel:
    connection: FakeConnection
    published: list[Published] = field(default_factory=list)
    acks: list[int] = field(default_factory=list)
    rejects: list[tuple[int, bool]] = field(default_factory=list)
    nacks: list[tuple[int, bool]] = field(default_factory=list)
    callback: Callable[..., None] | None = None
    confirms: bool = False
    closed: bool = False
    on_ack: Callable[[int], None] | None = None

    def exchange_declare(self, *args: Any, **kwargs: Any) -> None: ...
    def queue_declare(self, *args: Any, **kwargs: Any) -> None: ...
    def queue_bind(self, *args: Any, **kwargs: Any) -> None: ...
    def basic_qos(self, **kwargs: Any) -> None: ...

    def confirm_delivery(self) -> None:
        self.confirms = True

    def basic_consume(self, queue: str, on_message_callback: Callable[..., None]) -> None:
        self.callback = on_message_callback

    def basic_publish(
        self,
        exchange: str,
        routing_key: str,
        body: bytes,
        properties: Any = None,
        mandatory: bool = False,
    ) -> None:
        self.connection.record("basic_publish")
        if self.connection.publish_errors:
            raise self.connection.publish_errors.popleft()
        if self.connection.publish_gate is not None:
            self.connection.publish_gate.wait()
        self.published.append(
            Published(
                threading.current_thread(), exchange, routing_key, body, properties, mandatory
            )
        )
        self.connection.on_publish.set()

    def basic_ack(self, delivery_tag: int) -> None:
        if self.on_ack is not None:
            self.on_ack(delivery_tag)
        self.acks.append(delivery_tag)

    def basic_reject(self, delivery_tag: int, requeue: bool = True) -> None:
        self.rejects.append((delivery_tag, requeue))

    def basic_nack(self, delivery_tag: int, requeue: bool = True) -> None:
        self.nacks.append((delivery_tag, requeue))

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    """One connection. `deliver` queues a command; `process_data_events` hands it to the
    consumer callback on the calling thread, as pika does."""

    def __init__(self) -> None:
        self.channels: list[FakeChannel] = []
        self.calls: list[tuple[str, threading.Thread]] = []
        self.pending: deque[tuple[str, bytes]] = deque()
        self.publish_errors: deque[Exception] = deque()
        self.keepalive_errors: deque[Exception] = deque()
        self.publish_gate: threading.Event | None = None
        self.on_publish = threading.Event()
        self.on_poll: Callable[[], None] | None = None
        self.is_open = True
        self._tag = 0

    def record(self, name: str) -> None:
        self.calls.append((name, threading.current_thread()))

    def channel(self) -> FakeChannel:
        channel = FakeChannel(self)
        self.channels.append(channel)
        return channel

    def deliver(self, routing_key: str, body: bytes) -> None:
        self.pending.append((routing_key, body))

    def process_data_events(self, time_limit: float = 0) -> None:
        self.record("process_data_events")
        if self.keepalive_errors:
            raise self.keepalive_errors.popleft()
        if self.on_poll is not None:
            self.on_poll()
        channel = self.channels[-1] if self.channels else None
        while self.pending and channel is not None and channel.callback is not None:
            routing_key, body = self.pending.popleft()
            self._tag += 1
            method = SimpleNamespace(routing_key=routing_key, delivery_tag=self._tag)
            channel.callback(channel, method, None, body)

    def close(self) -> None:
        self.record("close")
        self.is_open = False


class FakeBroker:
    """Hands out FakeConnections, one per `connect` call, and remembers them."""

    def __init__(self) -> None:
        self.connections: list[FakeConnection] = []
        self.prepare: Callable[[FakeConnection], None] | None = None

    def connect(self, timeout_s: float, stop: threading.Event | None) -> FakeConnection:
        connection = FakeConnection()
        if self.prepare is not None:
            self.prepare(connection)
        self.connections.append(connection)
        return connection
