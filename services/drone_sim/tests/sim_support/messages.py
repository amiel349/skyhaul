"""Small builders for commands, telemetry and payloads used across the simulator tests."""

import json
import uuid
from typing import Any

from pydantic import BaseModel
from skyhaul_contracts import (
    AlertCode,
    AlertSeverity,
    CommandType,
    FlightState,
    Message,
    MessageType,
    MissionState,
    TelemetryType,
)
from skyhaul_contracts.v1 import (
    AlertPayload,
    ArmPayload,
    BatteryPayload,
    GotoPayload,
    HeartbeatPayload,
    LandPayload,
    MissionStatusPayload,
    PositionPayload,
    ReturnHomePayload,
    SetSpeedPayload,
    StartMissionPayload,
    TakeoffPayload,
    Waypoint,
)
from skyhaul_drone_sim.board import Job
from skyhaul_drone_sim.model import Drone
from skyhaul_drone_sim.outbox import Outgoing, new_message

DRONE = "main-d001"


def alert_payload(message: str = "low") -> AlertPayload:
    return AlertPayload(
        code=AlertCode.LOW_BATTERY, severity=AlertSeverity.WARNING, message=message, value=10.0
    )


def heartbeat(n: int = 0, armed: bool = False) -> HeartbeatPayload:
    return HeartbeatPayload(flight_state=FlightState.LANDED, armed=armed, uptime_s=n)


def minimal_payload(type: TelemetryType | str, n: int = 0) -> BaseModel:
    """A valid payload for a periodic telemetry type; `n` lands in a numeric field."""
    match TelemetryType(type):
        case TelemetryType.HEARTBEAT:
            return heartbeat(n)
        case TelemetryType.BATTERY:
            return BatteryPayload(charge_pct=100.0, est_remaining_s=n)
        case TelemetryType.POSITION:
            return PositionPayload(
                lat=32.0,
                lon=34.8,
                altitude_m=float(n),
                heading_deg=0.0,
                ground_speed_mps=0.0,
                target=None,
            )
        case TelemetryType.MISSION_STATUS:
            return MissionStatusPayload(
                mission_id="m1",
                state=MissionState.ACTIVE,
                waypoint_index=0,
                waypoints_total=1,
                progress_pct=0.0,
            )
        case other:
            raise ValueError(f"{other} is not a periodic job type")


def render_minimal(drone: Drone, job: Job, now: float) -> BaseModel:
    """A render function for any job: `values["n"]` (default 0) lands in the payload."""
    return minimal_payload(job.type, int(job.values.get("n", 0)))


def outgoing(
    type: TelemetryType | str = TelemetryType.HEARTBEAT,
    drone_id: str = DRONE,
    *,
    payload: BaseModel | None = None,
    n: int = 0,
    correlation_id: str | None = None,
    persistent: bool = False,
) -> Outgoing:
    """A telemetry Outgoing. ALERT needs an explicit `payload`."""
    body = payload if payload is not None else minimal_payload(type, n)
    message = new_message(MessageType(type), drone_id, body, correlation_id)
    return Outgoing(message, persistent=persistent)


def minimal_command_payload(type: CommandType | str) -> BaseModel:
    match CommandType(type):
        case CommandType.ARM:
            return ArmPayload()
        case CommandType.TAKEOFF:
            return TakeoffPayload(altitude_m=30.0)
        case CommandType.GOTO:
            return GotoPayload(lat=32.0, lon=34.8, altitude_m=30.0)
        case CommandType.RETURN_HOME:
            return ReturnHomePayload()
        case CommandType.LAND:
            return LandPayload()
        case CommandType.SET_SPEED:
            return SetSpeedPayload(speed_mps=5.0)
        case CommandType.START_MISSION:
            return StartMissionPayload(
                mission_id="m1", waypoints=[Waypoint(lat=32.0, lon=34.8, altitude_m=30.0)]
            )


def command(
    type: CommandType | str = CommandType.ARM,
    drone_id: str = DRONE,
    *,
    payload: BaseModel | None = None,
    correlation_id: str | None = None,
) -> Message[Any]:
    """A decoded command, as the consumer would hand it to `dispatch`."""
    if payload is None:
        payload = minimal_command_payload(type)
    return new_message(MessageType(type), drone_id, payload, correlation_id or str(uuid.uuid4()))


def payload_of(out: Outgoing) -> dict[str, Any]:
    """The payload as plain JSON data."""
    return out.message.payload.model_dump(mode="json", exclude_defaults=True)


def raw_command(
    type: str = "ARM",
    drone_id: str = DRONE,
    *,
    payload: dict[str, Any] | None = None,
    schema_version: Any = 1,
    msg_id: str | None = None,
    correlation_id: str | None = None,
    routing_key: str | None = None,
) -> tuple[str, bytes, dict[str, Any]]:
    """A hand-built command body, for types or versions the Codec refuses to encode.

    Returns `(routing_key, body, envelope)`. The routing key matches the envelope unless
    one is given."""
    envelope: dict[str, Any] = {
        "msg_id": msg_id or str(uuid.uuid4()),
        "correlation_id": correlation_id or str(uuid.uuid4()),
        "drone_id": drone_id,
        "type": type,
        "schema_version": schema_version,
        "ts": "2026-11-23T10:00:00.000Z",
        "payload": {} if payload is None else payload,
    }
    key = routing_key or f"drone.{drone_id}.{type}"
    return key, json.dumps(envelope).encode(), envelope
