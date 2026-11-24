"""The consumer: runs on the main thread, on its own connection.

For each delivery: decode, check the routing key, run one board transaction with one
`dispatch`, put the replies in the outbox (outside the lock), then ack. It never publishes.
The reaction to each kind of bad message follows design note section 3:

- not decodable, an invalid envelope, or a routing key that does not match the envelope:
  reject without requeue (dead-lettered to `fleet.dead`);
- unsupported version: NACK only when the routing key names the drone the hint claims,
  otherwise dead-letter;
- unknown type, invalid payload, a raising handler: NACK, then ack (never redelivered);
- unknown drone: no reply, counted, acked.

Every reply is stamped with its namespace's epoch under the board lock (inside the
dispatch transaction, or with `JobsBoard.stamp` for a NACK made before dispatch), so a
reply produced before a reset of its namespace is never published (ADR 0009).
"""

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Final

from pika.exceptions import AMQPChannelError, AMQPConnectionError
from skyhaul_contracts import (
    Codec,
    InvalidPayload,
    MalformedMessage,
    Message,
    NackReason,
    UnknownType,
    UnsupportedVersion,
)
from skyhaul_contracts.topology import SIM_COMMAND_QUEUE, declare_debug_tap, declare_topology

from skyhaul_drone_sim.amqp import Connect, close_quietly
from skyhaul_drone_sim.board import JobsBoard
from skyhaul_drone_sim.clock import Clock
from skyhaul_drone_sim.outbox import Outbox, Outgoing, put_until_stopped
from skyhaul_drone_sim.registry import Registry
from skyhaul_drone_sim.replies import nack

log = logging.getLogger(__name__)

POLL_S: Final = 0.2
"""How often the consume loop checks the stop event."""

RECONNECT_PAUSE_S: Final = 0.5

_ROUTING_KEY_MAX_LOGGED: Final = 128


@dataclass(frozen=True, slots=True)
class Outcome:
    """What to do with one delivery: put `replies` and ack, or reject to the DLX."""

    replies: list[Outgoing] = field(default_factory=list)
    dead_letter: bool = False


DEAD_LETTER: Final = Outcome(dead_letter=True)


def parse_routing_key(routing_key: str) -> tuple[str, str] | None:
    """`drone.<id>.<TYPE>` to `(id, TYPE)`, or None when it has another shape."""
    parts = routing_key.split(".")
    if len(parts) != 3 or parts[0] != "drone":
        return None
    return parts[1], parts[2]


class Consumer:
    def __init__(
        self,
        *,
        codec: Codec,
        board: JobsBoard,
        registry: Registry,
        outbox: Outbox,
        clock: Clock,
        stop: threading.Event,
        connect: Connect,
        connect_timeout_s: float,
        prefetch: int,
        debug_tap: bool = False,
    ) -> None:
        self._codec = codec
        self._board = board
        self._registry = registry
        self._outbox = outbox
        self._clock = clock
        self._stop = stop
        self._connect = connect
        self._connect_timeout_s = connect_timeout_s
        self._prefetch = prefetch
        self._debug_tap = debug_tap
        self.connected = threading.Event()
        """Set while the consumer is registered on the command queue (tests, `/healthz`)."""
        self._thread: threading.Thread | None = None

    def is_alive(self) -> bool:
        """True while `run()` is running on a live thread."""
        return self._thread is not None and self._thread.is_alive()

    # -- the consume loop --------------------------------------------------------------

    def run(self) -> None:
        """Consume until the stop event is set. Reconnects after a lost connection.

        Raises BrokerUnavailable when RabbitMQ cannot be reached within the bound.
        """
        self._thread = threading.current_thread()
        try:
            self._run()
        finally:
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            connection = self._connect(self._connect_timeout_s, self._stop)
            try:
                self._consume(connection)
            except (AMQPConnectionError, AMQPChannelError) as exc:
                # Unacked commands are requeued by the broker; reconnect and go on.
                log.warning(
                    "consumer connection lost, reconnecting", extra={"error": type(exc).__name__}
                )
            finally:
                self.connected.clear()
                close_quietly(connection)
            # A channel error right after connecting (e.g. PRECONDITION_FAILED) must not
            # turn into a tight reconnect loop.
            self._stop.wait(RECONNECT_PAUSE_S)

    def _consume(self, connection: Any) -> None:
        channel = connection.channel()
        declare_topology(channel)
        if self._debug_tap:
            declare_debug_tap(channel)
        channel.basic_qos(prefetch_count=self._prefetch)
        channel.basic_consume(SIM_COMMAND_QUEUE, on_message_callback=self.on_message)
        self.connected.set()
        log.info("consuming commands", extra={"queue": SIM_COMMAND_QUEUE})
        while not self._stop.is_set():
            connection.process_data_events(time_limit=POLL_S)
        # Closing the channel requeues the prefetched commands nobody acked.
        channel.close()

    def on_message(self, channel: Any, method: Any, properties: Any, body: bytes) -> None:
        """pika callback for one delivery. Never raises: a bad message cannot stop it."""
        if self._stop.is_set():
            return  # left unacked: requeued when the channel closes
        try:
            outcome = self.handle(method.routing_key, body)
        except Exception:
            log.exception("command handling failed; dead-lettering it")
            channel.basic_reject(method.delivery_tag, requeue=False)
            return
        if outcome.dead_letter:
            channel.basic_reject(method.delivery_tag, requeue=False)
            return
        for reply in outcome.replies:
            if not put_until_stopped(self._outbox, reply, self._stop):
                log.warning("stopping with a full outbox; the command will be redelivered")
                return
        channel.basic_ack(method.delivery_tag)

    # -- one message -------------------------------------------------------------------

    def handle(self, routing_key: str, body: bytes) -> Outcome:
        """Decide what one delivery produces. No I/O except logging, no lock held."""
        target = parse_routing_key(routing_key)
        if target is None:
            return _dead_letter("routing key is not drone.<id>.<TYPE>", routing_key)
        key_id, key_type = target
        try:
            command = self._codec.decode(body)
        except MalformedMessage as exc:
            return _dead_letter("malformed message", routing_key, exc.reason, exc.errors)
        except UnsupportedVersion as exc:
            return self._unsupported_version(exc, routing_key, key_id, key_type)
        except (UnknownType, InvalidPayload) as exc:
            envelope = exc.envelope
            if (envelope.drone_id, envelope.type) != (key_id, key_type):
                return _dead_letter("routing key does not match the envelope", routing_key)
            reason = (
                NackReason.UNKNOWN_TYPE
                if isinstance(exc, UnknownType)
                else NackReason.INVALID_PAYLOAD
            )
            log.warning(
                "command refused",
                extra={
                    "reason": reason.value,
                    "errors": exc.errors,
                    "correlation_id": envelope.correlation_id,
                    "drone_id": envelope.drone_id,
                },
            )
            return self._reply(
                nack(
                    drone_id=envelope.drone_id,
                    correlation_id=envelope.correlation_id,
                    command_msg_id=envelope.msg_id,
                    command_type=envelope.type,
                    reason=reason,
                    detail=exc.detail,
                )
            )
        if (command.drone_id, command.type.value) != (key_id, key_type):
            return _dead_letter("routing key does not match the envelope", routing_key)
        return self._dispatch(command)

    def _unsupported_version(
        self, exc: UnsupportedVersion, routing_key: str, key_id: str, key_type: str
    ) -> Outcome:
        hint = exc.hint
        # The hint comes from an envelope that could not be validated: NACK only the drone
        # the routing key names, so nobody can make us NACK in another drone's name.
        if hint.msg_id is None or hint.drone_id is None or hint.drone_id != key_id:
            return _dead_letter("unsupported version without a matching drone", routing_key)
        if hint.type is not None and hint.type != key_type:
            return _dead_letter("routing key does not match the envelope", routing_key)
        log.warning(
            "command refused",
            extra={
                "reason": NackReason.UNSUPPORTED_VERSION.value,
                "correlation_id": hint.correlation_id,
                "drone_id": hint.drone_id,
            },
        )
        return self._reply(
            nack(
                drone_id=hint.drone_id,
                correlation_id=hint.correlation_id,
                command_msg_id=hint.msg_id,
                command_type=hint.type or "",
                reason=NackReason.UNSUPPORTED_VERSION,
                detail=exc.detail,
            )
        )

    def _reply(self, reply: Outgoing) -> Outcome:
        return Outcome([self._board.stamp(reply)])

    def _dispatch(self, command: Message[Any]) -> Outcome:
        now = self._clock.monotonic()
        with self._board.transaction() as world:
            result = self._registry.dispatch(world, command, now)
            replies = world.stamp(result.replies)
        # Everything below runs after the board lock is released.
        context = {
            "correlation_id": command.correlation_id,
            "drone_id": command.drone_id,
            "type": command.type.value,
            "msg_id": command.msg_id,
        }
        if result.error is not None:
            error = result.error
            log.error(
                "command handler raised",
                exc_info=(type(error), error, error.__traceback__),
                extra=context,
            )
        elif result.unknown_drone:
            log.warning("command for an unknown drone, not answered", extra=context)
        elif result.duplicate:
            log.info("duplicate command, cached reply sent again", extra=context)
        else:
            log.info("command handled", extra=context)
        return Outcome(replies)


def _dead_letter(
    why: str,
    routing_key: str,
    reason: str | None = None,
    errors: tuple[str, ...] = (),
) -> Outcome:
    log.warning(
        "command dead-lettered: %s",
        why,
        extra={
            "routing_key": routing_key[:_ROUTING_KEY_MAX_LOGGED],
            "reason": reason,
            "errors": errors,
        },
    )
    return DEAD_LETTER
