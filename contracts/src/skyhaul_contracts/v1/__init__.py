"""schema_version 1 models, generated from contracts/schemas/v1 (see ``_generated``)."""

from skyhaul_contracts.v1._generated.commands.arm_schema import ArmPayload
from skyhaul_contracts.v1._generated.commands.goto_schema import GotoPayload
from skyhaul_contracts.v1._generated.commands.land_schema import LandPayload
from skyhaul_contracts.v1._generated.commands.return_home_schema import ReturnHomePayload
from skyhaul_contracts.v1._generated.commands.set_speed_schema import SetSpeedPayload
from skyhaul_contracts.v1._generated.commands.start_mission_schema import StartMissionPayload
from skyhaul_contracts.v1._generated.commands.takeoff_schema import TakeoffPayload
from skyhaul_contracts.v1._generated.common_schema import GeoPoint, Waypoint
from skyhaul_contracts.v1._generated.envelope_schema import Envelope
from skyhaul_contracts.v1._generated.telemetry.ack_schema import AckPayload
from skyhaul_contracts.v1._generated.telemetry.alert_schema import AlertPayload
from skyhaul_contracts.v1._generated.telemetry.battery_schema import BatteryPayload
from skyhaul_contracts.v1._generated.telemetry.heartbeat_schema import HeartbeatPayload
from skyhaul_contracts.v1._generated.telemetry.mission_status_schema import MissionStatusPayload
from skyhaul_contracts.v1._generated.telemetry.nack_schema import NackPayload
from skyhaul_contracts.v1._generated.telemetry.position_schema import PositionPayload

__all__ = [
    "AckPayload",
    "AlertPayload",
    "ArmPayload",
    "BatteryPayload",
    "Envelope",
    "GeoPoint",
    "GotoPayload",
    "HeartbeatPayload",
    "LandPayload",
    "MissionStatusPayload",
    "NackPayload",
    "PositionPayload",
    "ReturnHomePayload",
    "SetSpeedPayload",
    "StartMissionPayload",
    "TakeoffPayload",
    "Waypoint",
]
