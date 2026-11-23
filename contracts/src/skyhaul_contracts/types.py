"""Message types, enums and the (schema_version, type) -> payload model catalogue.

The enums are generated from ``common.schema.json`` and re-exported here, so the Python
values cannot drift from the schema. The contract suite checks that ``MessageType`` is
exactly ``CommandType`` plus ``TelemetryType`` and that the catalogue covers every type.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from skyhaul_contracts import v1
from skyhaul_contracts._base import ContractModel
from skyhaul_contracts.v1._generated.common_schema import (
    AlertCode,
    AlertSeverity,
    CommandType,
    FlightState,
    MessageType,
    MissionState,
    NackReason,
    TelemetryType,
)

CURRENT_VERSION: Final = 1
"""The version every encoder writes."""

SUPPORTED_VERSIONS: Final = frozenset({CURRENT_VERSION})
"""Versions every decoder reads: N-1 and N (ADR 0005). Only 1 exists so far."""

ENVELOPES: Final[Mapping[int, type[v1.Envelope]]] = MappingProxyType({1: v1.Envelope})

PAYLOADS: Final[Mapping[tuple[int, MessageType], type[ContractModel]]] = MappingProxyType(
    {
        (1, MessageType.ARM): v1.ArmPayload,
        (1, MessageType.TAKEOFF): v1.TakeoffPayload,
        (1, MessageType.GOTO): v1.GotoPayload,
        (1, MessageType.RETURN_HOME): v1.ReturnHomePayload,
        (1, MessageType.LAND): v1.LandPayload,
        (1, MessageType.SET_SPEED): v1.SetSpeedPayload,
        (1, MessageType.START_MISSION): v1.StartMissionPayload,
        (1, MessageType.ACK): v1.AckPayload,
        (1, MessageType.NACK): v1.NackPayload,
        (1, MessageType.HEARTBEAT): v1.HeartbeatPayload,
        (1, MessageType.BATTERY): v1.BatteryPayload,
        (1, MessageType.POSITION): v1.PositionPayload,
        (1, MessageType.MISSION_STATUS): v1.MissionStatusPayload,
        (1, MessageType.ALERT): v1.AlertPayload,
    }
)

COMMAND_TYPES: Final = frozenset(MessageType(t.value) for t in CommandType)
TELEMETRY_TYPES: Final = frozenset(MessageType(t.value) for t in TelemetryType)


def is_command(type: MessageType) -> bool:
    """True for messages sent to a drone (fleet.cmd), False for telemetry."""
    return type in COMMAND_TYPES


__all__ = [
    "COMMAND_TYPES",
    "CURRENT_VERSION",
    "ENVELOPES",
    "PAYLOADS",
    "SUPPORTED_VERSIONS",
    "TELEMETRY_TYPES",
    "AlertCode",
    "AlertSeverity",
    "CommandType",
    "FlightState",
    "MessageType",
    "MissionState",
    "NackReason",
    "TelemetryType",
    "is_command",
]
