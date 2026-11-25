"""Per message type: the telemetry row to store and the latest-state update for Redis."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from pydantic import BaseModel
from skyhaul_contracts.message import Message, parse_ts
from skyhaul_contracts.types import MessageType
from skyhaul_fleet_core.models import CommandStatus

STORED: Final = frozenset(
    {MessageType.POSITION, MessageType.BATTERY, MessageType.MISSION_STATUS, MessageType.ALERT}
)


@dataclass(frozen=True)
class StateUpdate:
    """One field group of ``drone:{id}``, written only if ``ts`` is newer than the group's."""

    group: str
    ts: str
    fields: dict[str, Any]


@dataclass(frozen=True)
class Reply:
    """A drone's ACK or NACK, applied to the command it names (if that drone sent it)."""

    command_id: uuid.UUID
    drone_id: str
    status: CommandStatus
    replied_at: datetime
    nack_reason: str | None = None
    nack_detail: str | None = None


@dataclass(frozen=True)
class Handled:
    row: dict[str, Any] | None
    state: StateUpdate | None
    reply: Reply | None = None


def handle(msg: Message[BaseModel], received_at: datetime) -> Handled:
    """Stored types give a row, HEARTBEAT gives state only, ACK and NACK give a reply."""
    payload = msg.payload.model_dump(mode="json")
    row = _row(msg, payload, received_at) if msg.type in STORED else None
    return Handled(row, _state(msg.type, msg.ts, payload), _reply(msg, payload, received_at))


def _reply(msg: Message[BaseModel], payload: dict[str, Any], received_at: datetime) -> Reply | None:
    if msg.type not in (MessageType.ACK, MessageType.NACK):
        return None
    command_id = uuid.UUID(payload["command_msg_id"])
    if msg.type is MessageType.ACK:
        return Reply(command_id, msg.drone_id, CommandStatus.ACKED, received_at)
    return Reply(
        command_id,
        msg.drone_id,
        CommandStatus.NACKED,
        received_at,
        # Plain text data, stored as is and never interpreted.
        nack_reason=payload["reason"],
        nack_detail=payload["detail"],
    )


def _row(msg: Message[BaseModel], payload: dict[str, Any], received_at: datetime) -> dict[str, Any]:
    return {
        "msg_id": uuid.UUID(msg.msg_id),
        "drone_id": msg.drone_id,
        "type": str(msg.type),
        "ts": parse_ts(msg.ts),
        "received_at": received_at,
        "correlation_id": uuid.UUID(msg.correlation_id) if msg.correlation_id else None,
        "payload": payload,
    }


def _state(type: MessageType, ts: str, p: dict[str, Any]) -> StateUpdate | None:
    match type:
        case MessageType.HEARTBEAT:
            fields = {"flight_state": p["flight_state"], "armed": p["armed"]}
            return StateUpdate("hb", ts, {**fields, "last_heartbeat_at": ts})
        case MessageType.POSITION:
            names = ("lat", "lon", "altitude_m", "heading_deg", "ground_speed_mps", "target")
            return StateUpdate("pos", ts, {n: p[n] for n in names})
        case MessageType.BATTERY:
            fields = {"battery_pct": p["charge_pct"], "est_remaining_s": p["est_remaining_s"]}
            return StateUpdate("bat", ts, fields)
        case MessageType.MISSION_STATUS:
            return StateUpdate("mis", ts, {"mission_id": p["mission_id"]})
        case _:
            return None
