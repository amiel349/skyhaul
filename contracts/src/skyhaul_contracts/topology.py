"""RabbitMQ names, routing keys and the one place that declares exchanges and queues.

Every service declares through ``declare_topology``, so they can never disagree on queue
arguments (RabbitMQ answers a mismatched re-declare with PRECONDITION_FAILED). Declaring is
idempotent: same names, same arguments, any number of times.
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

SIM_COMMAND_QUEUE: Final = "drone_sim.cmd"
SIM_COMMAND_QUEUE_ARGUMENTS: Final[Mapping[str, Any]] = MappingProxyType(
    {
        "x-queue-type": "quorum",
        "x-single-active-consumer": True,
        "x-delivery-limit": 5,
        "x-dead-letter-exchange": DEAD_LETTER_EXCHANGE,
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
    channel.queue_declare(DEAD_LETTER_QUEUE, durable=True)
    channel.queue_bind(DEAD_LETTER_QUEUE, DEAD_LETTER_EXCHANGE)
    channel.queue_declare(
        SIM_COMMAND_QUEUE, durable=True, arguments=dict(SIM_COMMAND_QUEUE_ARGUMENTS)
    )
    channel.queue_bind(SIM_COMMAND_QUEUE, COMMAND_EXCHANGE, routing_key=ALL_DRONES_BINDING)


def declare_debug_tap(channel: Any) -> None:
    """Optional bounded telemetry queue for watching traffic in the management UI."""
    channel.queue_declare(DEBUG_TAP_QUEUE, durable=False, arguments=dict(DEBUG_TAP_QUEUE_ARGUMENTS))
    channel.queue_bind(DEBUG_TAP_QUEUE, TELEMETRY_EXCHANGE, routing_key=ALL_DRONES_BINDING)
