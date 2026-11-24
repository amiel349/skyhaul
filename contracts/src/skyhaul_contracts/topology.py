"""RabbitMQ names, routing keys and the one place that declares exchanges and queues.

Everything that declares goes through these helpers, so declarers can never disagree on queue
arguments (RabbitMQ answers a mismatched re-declare with PRECONDITION_FAILED). Declaring is
idempotent: same names, same arguments, any number of times. From M2 the one-shot
``broker-init`` declares the whole topology and the fleet services declare nothing; the
simulator, the test clients and the MCP server still declare what they need (ADR 0013).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Final

from skyhaul_contracts.types import MessageType, is_command

COMMAND_EXCHANGE: Final = "fleet.cmd"
TELEMETRY_EXCHANGE: Final = "fleet.telemetry"
DEAD_LETTER_EXCHANGE: Final = "fleet.dlx"
DEAD_LETTER_QUEUE: Final = "fleet.dead"

ALL_DRONES_BINDING: Final = "drone.*.*"

# Queue arguments cannot change after the first declare (PRECONDITION_FAILED), so the length
# bounds are part of the contract from v1 on (ADR 0006).
MAX_QUEUE_LENGTH: Final = 10_000
# A byte bound too: x-max-length counts messages only, and a publisher that skips the Codec
# could otherwise fill a queue with very large bodies. 10,000 messages of at most
# MAX_MESSAGE_BYTES (65,536) each; a unit test keeps this equal to that product.
MAX_QUEUE_BYTES: Final = 655_360_000

DEAD_LETTER_QUEUE_ARGUMENTS: Final[Mapping[str, Any]] = MappingProxyType(
    # Inspected by hand; under a flood keep the newest evidence and drop the oldest.
    {
        "x-max-length": MAX_QUEUE_LENGTH,
        "x-max-length-bytes": MAX_QUEUE_BYTES,
        "x-overflow": "drop-head",
    }
)

SIM_COMMAND_QUEUE: Final = "drone_sim.cmd"
SIM_COMMAND_QUEUE_ARGUMENTS: Final[Mapping[str, Any]] = MappingProxyType(
    {
        "x-queue-type": "quorum",
        "x-single-active-consumer": True,
        "x-delivery-limit": 5,
        "x-dead-letter-exchange": DEAD_LETTER_EXCHANGE,
        # Commands are never dropped silently: when full, the broker nacks new publishes,
        # so a sender using publisher confirms sees back-pressure.
        "x-max-length": MAX_QUEUE_LENGTH,
        "x-max-length-bytes": MAX_QUEUE_BYTES,
        "x-overflow": "reject-publish",
    }
)

# The ingestor consumes every telemetry message, ACK and NACK included (ADR 0010, 0013).
INGESTOR_QUEUE: Final = "ingestor.telemetry"
INGESTOR_MAX_QUEUE_LENGTH: Final = 100_000
# 100,000 messages of at most MAX_MESSAGE_BYTES each; a unit test keeps this equal.
INGESTOR_MAX_QUEUE_BYTES: Final = 6_553_600_000
INGESTOR_QUEUE_ARGUMENTS: Final[Mapping[str, Any]] = MappingProxyType(
    {
        "x-queue-type": "quorum",
        # A second replica waits as a hot standby instead of splitting the stream.
        "x-single-active-consumer": True,
        "x-delivery-limit": 5,
        "x-dead-letter-exchange": DEAD_LETTER_EXCHANGE,
        "x-max-length": INGESTOR_MAX_QUEUE_LENGTH,
        "x-max-length-bytes": INGESTOR_MAX_QUEUE_BYTES,
        # A stopped ingestor must never push back on the simulator's publisher (its nack
        # retries would stall the outbox, and commands with it). The oldest telemetry goes
        # first; a lost ACK shows up as a TIMED_OUT command (ADR 0013).
        "x-overflow": "drop-head",
    }
)

DEBUG_TAP_QUEUE: Final = "debug.telemetry"
DEBUG_TAP_QUEUE_ARGUMENTS: Final[Mapping[str, Any]] = MappingProxyType(
    {"x-max-length": 1000, "x-overflow": "drop-head"}
)

# Same rules as DroneId and TypeName in common.schema.json (a contract test keeps them equal).
DRONE_ID_RE: Final = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
TYPE_NAME_RE: Final = re.compile(r"[A-Z][A-Z0-9_]{0,31}")


def routing_key(drone_id: str, type: str) -> str:
    """``drone.<id>.<TYPE>``, e.g. ``drone.gw0-d001.ARM``. Both parts must be one safe word."""
    if not DRONE_ID_RE.fullmatch(drone_id):
        raise ValueError("drone_id is not a valid routing-key word")
    if not TYPE_NAME_RE.fullmatch(type):
        raise ValueError("type is not a valid routing-key word")
    return f"drone.{drone_id}.{type}"


def drone_binding(drone_id: str) -> str:
    """Binding key for every message of one drone: ``drone.<id>.*``."""
    if not DRONE_ID_RE.fullmatch(drone_id):
        raise ValueError("drone_id is not a valid routing-key word")
    return f"drone.{drone_id}.*"


def exchange_for(type: MessageType) -> str:
    """Commands go to fleet.cmd, telemetry to fleet.telemetry."""
    return COMMAND_EXCHANGE if is_command(type) else TELEMETRY_EXCHANGE


def declare_topology(channel: Any) -> None:
    """Declare the shared exchanges, the dead-letter path and the simulator command queue.

    Command senders call this too, so a command published before the simulator first
    starts is queued instead of dropped. ``channel`` is a pika ``BlockingChannel``.
    """
    channel.exchange_declare(COMMAND_EXCHANGE, exchange_type="topic", durable=True)
    channel.exchange_declare(TELEMETRY_EXCHANGE, exchange_type="topic", durable=True)
    channel.exchange_declare(DEAD_LETTER_EXCHANGE, exchange_type="fanout", durable=True)
    channel.queue_declare(
        DEAD_LETTER_QUEUE, durable=True, arguments=dict(DEAD_LETTER_QUEUE_ARGUMENTS)
    )
    channel.queue_bind(DEAD_LETTER_QUEUE, DEAD_LETTER_EXCHANGE)
    channel.queue_declare(
        SIM_COMMAND_QUEUE, durable=True, arguments=dict(SIM_COMMAND_QUEUE_ARGUMENTS)
    )
    channel.queue_bind(SIM_COMMAND_QUEUE, COMMAND_EXCHANGE, routing_key=ALL_DRONES_BINDING)


def declare_ingestor_queue(channel: Any) -> None:
    """Declare the ingestor's queue, bound to every drone message on fleet.telemetry.

    In M2 ``broker-init`` calls this together with ``declare_topology``; the ingestor itself
    has no configure permission and declares nothing (ADR 0013). Idempotent.
    """
    channel.queue_declare(INGESTOR_QUEUE, durable=True, arguments=dict(INGESTOR_QUEUE_ARGUMENTS))
    channel.queue_bind(INGESTOR_QUEUE, TELEMETRY_EXCHANGE, routing_key=ALL_DRONES_BINDING)


def declare_debug_tap(channel: Any) -> None:
    """Optional bounded telemetry queue for watching traffic in the management UI."""
    channel.queue_declare(DEBUG_TAP_QUEUE, durable=False, arguments=dict(DEBUG_TAP_QUEUE_ARGUMENTS))
    channel.queue_bind(DEBUG_TAP_QUEUE, TELEMETRY_EXCHANGE, routing_key=ALL_DRONES_BINDING)
