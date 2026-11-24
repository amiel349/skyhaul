"""The ground station: commands and telemetry on RabbitMQ, always through the Codec (ADR 0007).

Every call opens its own short-lived connection, so a call runs on one worker thread with
one connection and nothing is shared between calls. Before sending or reading, it declares
an exclusive, auto-delete queue bound to `drone.<id>.*` on `fleet.telemetry`, so no reply
is missed. It declares the two exchanges, but never the simulator's command queue: with
`mandatory=True`, a command sent while that queue does not exist comes back unroutable at
once instead of waiting for a timeout.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Final

import pika
import pika.exceptions
from pydantic import BaseModel, ValidationError
from skyhaul_contracts import (
    CURRENT_VERSION,
    Codec,
    CodecError,
    CommandType,
    DecodeError,
    Message,
    MessageType,
)
from skyhaul_contracts.topology import (
    COMMAND_EXCHANGE,
    DRONE_ID_RE,
    TELEMETRY_EXCHANGE,
    drone_binding,
)
from skyhaul_contracts.types import PAYLOADS

from skyhaul_mcp_sim.errors import ToolFailure

log = logging.getLogger(__name__)

POLL_S: Final = 0.1
MAX_ERRORS: Final = 5
REPLY_TYPES: Final = frozenset({MessageType.ACK, MessageType.NACK})
NO_REPLY_EXPLANATION: Final = (
    "No ACK or NACK arrived in time. The broker routed the command to the simulator's "
    "queue, so this is not a delivery error. Likely causes: a LINK_LOSS fault (the drone "
    "drops commands silently), a REPLY_DELAY fault (replies can be held up to 30 s, so "
    "retry with a larger timeout_s), an unknown drone id, or a simulator that is stopped "
    "while its durable queue still exists. Check the drone with get_drone."
)

type Connect = Callable[[pika.ConnectionParameters], Any]
type Params = Callable[[], pika.ConnectionParameters]


@dataclass
class _Tap:
    """An exclusive queue on one drone's messages and the decoded messages it received."""

    connection: Any
    channel: Any
    codec: Codec
    received: list[Message[BaseModel]] = field(default_factory=list)
    rejected: int = 0

    def on_message(self, channel: Any, method: Any, properties: Any, body: bytes) -> None:
        try:
            self.received.append(self.codec.decode(body))
        except DecodeError as exc:
            log.warning("undecodable message dropped", extra={"reason": exc.reason})
            self.rejected += 1
            channel.basic_nack(method.delivery_tag, requeue=False)
            return
        channel.basic_ack(method.delivery_tag)

    def pump(self, deadline: float, done: Callable[[], bool]) -> None:
        while not done():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self.connection.process_data_events(time_limit=min(remaining, POLL_S))


class GroundStation:
    def __init__(
        self, params: Params, connect: Connect = pika.BlockingConnection, codec: Codec | None = None
    ) -> None:
        self._params = params
        self._connect = connect
        self._codec = codec or Codec()

    def send_command(
        self, drone_id: str, type: CommandType, payload: dict[str, Any], timeout_s: float
    ) -> dict[str, Any]:
        """Publish one command and wait for the ACK or NACK whose command_msg_id matches."""
        msg = self._build(drone_id, MessageType(type.value), payload)
        body = self._codec.encode(msg)
        routing_key = Codec.routing_key(drone_id, msg.type.value)
        sent = {"msg_id": msg.msg_id, "type": msg.type.value, "drone_id": drone_id}
        log_extra = {"correlation_id": msg.correlation_id, "drone_id": drone_id}
        with self._tap(drone_id) as tap:
            try:
                tap.channel.basic_publish(
                    COMMAND_EXCHANGE,
                    routing_key,
                    body,
                    properties=Codec.amqp_properties(msg, persistent=True),
                    mandatory=True,
                )
            except pika.exceptions.UnroutableError:
                raise ToolFailure(
                    "unroutable",
                    f"no queue is bound for {routing_key} on {COMMAND_EXCHANGE}: the "
                    "simulator has not declared its command queue on this broker. "
                    "Start it with make sim.",
                    command=sent,
                ) from None
            except pika.exceptions.NackError:
                raise ToolFailure(
                    "refused",
                    "the broker refused the command (the simulator's queue is full); "
                    "nothing was sent",
                    command=sent,
                ) from None
            log.info("command sent", extra={**log_extra, "type": msg.type.value})
            reply = self._wait_reply(tap, msg.msg_id, time.monotonic() + timeout_s)
        if reply is None:
            log.info("no reply", extra=log_extra)
            return {
                "result": "no_reply",
                "command": sent,
                "waited_s": timeout_s,
                "explanation": NO_REPLY_EXPLANATION,
            }
        return {"result": reply.type.value, "command": sent, "reply": _data(reply)}

    def read_telemetry(
        self,
        drone_id: str,
        types: frozenset[MessageType] | None,
        seconds: float,
        max_messages: int,
    ) -> dict[str, Any]:
        """Messages from one drone for `seconds`, at most `max_messages`, oldest first."""
        _check_drone_id(drone_id)
        with self._tap(drone_id) as tap:

            def wanted() -> list[Message[BaseModel]]:
                return [m for m in tap.received if types is None or m.type in types]

            tap.pump(time.monotonic() + seconds, lambda: len(wanted()) >= max_messages)
            messages = wanted()[:max_messages]
        return {
            "drone_id": drone_id,
            "seconds": seconds,
            "count": len(messages),
            "messages": [_data(m) for m in messages],
        }

    # -- helpers -------------------------------------------------------------------------

    def _build(
        self, drone_id: str, type: MessageType, payload: dict[str, Any]
    ) -> Message[BaseModel]:
        _check_drone_id(drone_id)
        model = PAYLOADS[(CURRENT_VERSION, type)]
        try:
            validated = model.model_validate(payload, strict=True)
        except ValidationError as exc:
            raise ToolFailure(
                "invalid_payload",
                f"the {type.value} payload breaks its schema; nothing was sent",
                errors=_summarize(exc),
            ) from None
        try:
            return self._codec.new(type, drone_id, validated, str(uuid.uuid4()))
        except CodecError:
            raise ToolFailure(
                "invalid_payload", f"the {type.value} command did not encode; nothing was sent"
            ) from None

    @contextmanager
    def _tap(self, drone_id: str) -> Iterator[_Tap]:
        params = self._params()
        try:
            connection = self._connect(params)
        except (
            pika.exceptions.ProbableAuthenticationError,
            pika.exceptions.ProbableAccessDeniedError,
        ):
            raise ToolFailure(
                "broker_refused",
                "RabbitMQ refused the login: check RABBITMQ_USER and RABBITMQ_PASSWORD",
            ) from None
        except (pika.exceptions.AMQPError, OSError):
            raise ToolFailure(
                "broker_unreachable",
                "RabbitMQ at RABBITMQ_HOST:RABBITMQ_PORT did not answer; is it running (make sim)?",
            ) from None
        try:
            channel = connection.channel()
            channel.confirm_delivery()
            channel.exchange_declare(COMMAND_EXCHANGE, exchange_type="topic", durable=True)
            channel.exchange_declare(TELEMETRY_EXCHANGE, exchange_type="topic", durable=True)
            queue = channel.queue_declare("", exclusive=True, auto_delete=True).method.queue
            channel.queue_bind(queue, TELEMETRY_EXCHANGE, routing_key=drone_binding(drone_id))
            tap = _Tap(connection, channel, self._codec)
            channel.basic_consume(queue, on_message_callback=tap.on_message)
            yield tap
        except (pika.exceptions.AMQPError, OSError) as exc:
            raise ToolFailure(
                "broker_error", f"RabbitMQ failed during the call ({type(exc).__name__})"
            ) from None
        finally:
            _close(connection)

    def _wait_reply(
        self, tap: _Tap, command_msg_id: str, deadline: float
    ) -> Message[BaseModel] | None:
        def match() -> Message[BaseModel] | None:
            for m in tap.received:
                if m.type in REPLY_TYPES and str(getattr(m.payload, "command_msg_id", "")) == (
                    command_msg_id
                ):
                    return m
            return None

        tap.pump(deadline, lambda: match() is not None)
        return match()


def _check_drone_id(drone_id: str) -> None:
    if not DRONE_ID_RE.fullmatch(drone_id):
        raise ToolFailure("invalid_drone_id", "drone_id must match ^[a-z0-9][a-z0-9-]{0,63}$")


def _summarize(exc: ValidationError) -> list[str]:
    """`<field path>: <error kind>`, never the input values."""
    errors = exc.errors(include_url=False, include_input=False, include_context=False)
    return [
        f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['type']}"
        for e in errors[:MAX_ERRORS]
    ]


def _data(msg: Message[BaseModel]) -> dict[str, Any]:
    return msg.model_dump(mode="json", exclude_none=True)


def _close(connection: Any) -> None:
    try:
        if connection.is_open:
            connection.close()
    except (pika.exceptions.AMQPError, OSError):
        log.warning("closing the broker connection failed")
