"""Per message type: the telemetry row to store, the latest-state update for Redis, and what
the message means for commands (ACK, NACK), missions (MISSION_STATUS), alerts (ALERT) and the
link watchdog (HEARTBEAT)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from pydantic import BaseModel
from skyhaul_contracts.message import Message, parse_ts
from skyhaul_contracts.types import MessageType
from skyhaul_fleet_core.models import (
    AlertCode,
    AlertSeverity,
    AlertSource,
    CommandStatus,
    MissionState,
)

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
class MissionUpdate:
    """A drone's MISSION_STATUS, applied to the mission it names (if that drone flies it)."""

    mission_id: uuid.UUID | None  # None: not a mission id the API issued (not a UUID)
    drone_id: str
    state: MissionState
    waypoint_index: int
    progress_pct: float


@dataclass(frozen=True)
class Handled:
    row: dict[str, Any] | None
    state: StateUpdate | None
    reply: Reply | None = None
    mission: MissionUpdate | None = None
    alert: dict[str, Any] | None = None
    heartbeat: bool = False


def handle(msg: Message[BaseModel], received_at: datetime) -> Handled:
    """Stored types give a row, HEARTBEAT gives state and a heartbeat, ACK and NACK give a
    reply, MISSION_STATUS a mission update and ALERT an alert row."""
    payload = msg.payload.model_dump(mode="json")
    row = _row(msg, payload, received_at) if msg.type in STORED else None
    return Handled(
        row,
        _state(msg.type, msg.ts, payload),
        _reply(msg, payload, received_at),
        _mission(msg, payload),
        _alert(msg, payload),
        heartbeat=msg.type is MessageType.HEARTBEAT,
    )


def _mission(msg: Message[BaseModel], payload: dict[str, Any]) -> MissionUpdate | None:
    if msg.type is not MessageType.MISSION_STATUS:
        return None
    return MissionUpdate(
        _uuid_or_none(payload["mission_id"]),
        msg.drone_id,
        MissionState(payload["state"]),
        payload["waypoint_index"],
        payload["progress_pct"],
    )


def _alert(msg: Message[BaseModel], payload: dict[str, Any]) -> dict[str, Any] | None:
    """A drone alert row; ``message`` is plain text data, stored as is."""
    if msg.type is not MessageType.ALERT:
        return None
    return {
        "msg_id": uuid.UUID(msg.msg_id),
        "drone_id": msg.drone_id,
        "code": AlertCode(payload["code"]),
        "severity": AlertSeverity(payload["severity"]),
        "message": payload["message"],
        "value": payload["value"],
        "source": AlertSource.DRONE,
        "raised_at": parse_ts(msg.ts),
    }


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


def _uuid_or_none(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
