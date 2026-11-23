"""The test command client: sends commands the way a real ground station must, and waits for
the ACK or NACK that answers each one.

- It calls `declare_topology` first, so a command sent before the simulator starts is
  queued, not unroutable.
- It publishes through the Codec, persistent, with `mandatory=True` and publisher confirms:
  an unroutable command raises `UnroutableError`, a refused one (full queue) `NackError`.
- Replies arrive on an exclusive, auto-delete queue bound to `drone.<id>.*` for each drone
  it watches, and are matched by `command_msg_id`.

It is single-threaded, like the pika connection it owns.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

import pika
from pydantic import BaseModel
from skyhaul_contracts import Codec, Message, MessageType
from skyhaul_contracts.topology import (
    COMMAND_EXCHANGE,
    TELEMETRY_EXCHANGE,
    declare_topology,
    drone_binding,
)

REPLY_TYPES = frozenset({MessageType.ACK, MessageType.NACK})


@dataclass(frozen=True)
class Reply:
    message: Message[Any]
    properties: Any

    @property
    def command_msg_id(self) -> str:
        return str(self.message.payload.command_msg_id)


class CommandClient:
    def __init__(self, params: pika.ConnectionParameters, codec: Codec | None = None) -> None:
        self.codec = codec or Codec()
        self._connection = pika.BlockingConnection(params)
        self._channel = self._connection.channel()
        declare_topology(self._channel)
        self._channel.confirm_delivery()
        result = self._channel.queue_declare("", exclusive=True, auto_delete=True)
        self._queue: str = result.method.queue
        self._channel.basic_consume(self._queue, self._on_reply, auto_ack=True)
        self.replies: list[Reply] = []
        """Every ACK and NACK received, in arrival order."""

    def close(self) -> None:
        if self._connection.is_open:
            self._connection.close()

    def __enter__(self) -> CommandClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def watch(self, drone_id: str) -> None:
        """Receive every message from `drone_id`."""
        self._channel.queue_bind(
            self._queue, TELEMETRY_EXCHANGE, routing_key=drone_binding(drone_id)
        )

    # -- sending -----------------------------------------------------------------------

    def send(
        self,
        type: MessageType,
        drone_id: str,
        payload: BaseModel,
        correlation_id: str | None = None,
    ) -> Message[BaseModel]:
        """Publish one command through the Codec. Returns the message that was sent."""
        msg = self.codec.new(type, drone_id, payload, correlation_id or str(uuid.uuid4()))
        self._channel.basic_publish(
            COMMAND_EXCHANGE,
            Codec.routing_key(drone_id, type.value),
            self.codec.encode(msg),
            properties=Codec.amqp_properties(msg, persistent=True),
            mandatory=True,
        )
        return msg

    def send_raw(self, routing_key: str, body: bytes) -> None:
        """Publish bytes the Codec would refuse to build (garbage, unknown types)."""
        self._channel.basic_publish(
            COMMAND_EXCHANGE,
            routing_key,
            body,
            properties=pika.BasicProperties(delivery_mode=pika.DeliveryMode.Persistent),
            mandatory=True,
        )

    # -- waiting -----------------------------------------------------------------------

    def wait_reply(self, command_msg_id: str, timeout_s: float = 10.0) -> Reply:
        """The first ACK or NACK for `command_msg_id`. Fails at the deadline."""
        deadline = time.monotonic() + timeout_s
        while True:
            for reply in self.replies:
                if reply.command_msg_id == command_msg_id:
                    return reply
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no reply to {command_msg_id} in {timeout_s} s")
            self._connection.process_data_events(time_limit=min(remaining, 0.1))

    def wait_replies(self, count: int, timeout_s: float = 30.0) -> list[Reply]:
        """Wait until at least `count` replies arrived; return them in arrival order."""
        deadline = time.monotonic() + timeout_s
        while len(self.replies) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{len(self.replies)} of {count} replies in {timeout_s} s")
            self._connection.process_data_events(time_limit=min(remaining, 0.1))
        return list(self.replies)

    def pump(self, seconds: float) -> None:
        """Process incoming messages for `seconds` (to prove that nothing arrives)."""
        deadline = time.monotonic() + seconds
        while (remaining := deadline - time.monotonic()) > 0:
            self._connection.process_data_events(time_limit=min(remaining, 0.1))

    def _on_reply(self, channel: Any, method: Any, properties: Any, body: bytes) -> None:
        message = self.codec.decode(body)
        if message.type in REPLY_TYPES:
            self.replies.append(Reply(message, properties))
