"""One handler per `CommandType`, with the preconditions of the message catalogue (design
note section 2).

A handler checks its preconditions before changing anything, so a refusal is a plain NACK
return. ACK means accepted, not completed: completion shows up in telemetry. Payload ranges
(speed, altitude, waypoint count) are already enforced by the Codec, which answers a bad
payload with NACK INVALID_PAYLOAD before any handler runs. Handlers never publish, sleep or
log: they return their replies, and `dispatch` runs them inside one board transaction.
"""

from typing import Any, Final

from skyhaul_contracts import CommandType, FlightState, Message, NackReason
from skyhaul_contracts.v1 import GotoPayload, SetSpeedPayload, StartMissionPayload, TakeoffPayload

from skyhaul_drone_sim import behaviors
from skyhaul_drone_sim.board import World
from skyhaul_drone_sim.model import Drone, GeoPoint, Target
from skyhaul_drone_sim.outbox import Outgoing
from skyhaul_drone_sim.registry import Registry
from skyhaul_drone_sim.replies import ack, nack_command

MIN_ARM_BATTERY_PCT: Final = 15.0

NOT_LANDED: Final = "the drone is not landed"
NOT_ARMED: Final = "the drone is not armed on the ground"
NOT_AIRBORNE: Final = "the drone is not airborne"
NOT_ARMED_OR_AIRBORNE: Final = "the drone is neither armed nor airborne"
MISSION_RUNNING: Final = "a mission is active"
BATTERY_LOW: Final = "battery below 15 percent"
BATTERY_CRITICAL: Final = "battery at or below 10 percent: the drone only returns home or lands"
TARGET_OUTSIDE: Final = "the target is outside the geofence"
WAYPOINT_OUTSIDE: Final = "a waypoint is outside the geofence"

registry = Registry()


@registry.handles(CommandType.ARM)
def handle_arm(world: World, command: Message[Any], now: float) -> list[Outgoing]:
    d = _drone(world, command)
    if d.state is not FlightState.LANDED:
        return [nack_command(command, NackReason.INVALID_STATE, NOT_LANDED)]
    if d.battery_pct < MIN_ARM_BATTERY_PCT:
        return [nack_command(command, NackReason.BATTERY_TOO_LOW, BATTERY_LOW)]
    d.state = FlightState.ARMED
    return [ack(command)]


@registry.handles(CommandType.TAKEOFF)
def handle_takeoff(world: World, command: Message[Any], now: float) -> list[Outgoing]:
    d = _drone(world, command)
    payload: TakeoffPayload = command.payload
    if d.state is not FlightState.ARMED:
        return [nack_command(command, NackReason.INVALID_STATE, NOT_ARMED)]
    behaviors.fly_to(d, Target(d.pos, payload.altitude_m), FlightState.TAKING_OFF)
    _moved_by(world, command, now)
    return [ack(command)]


@registry.handles(CommandType.GOTO)
def handle_goto(world: World, command: Message[Any], now: float) -> list[Outgoing]:
    d = _drone(world, command)
    payload: GotoPayload = command.payload
    point = GeoPoint(payload.lat, payload.lon)
    if not d.airborne:
        return [nack_command(command, NackReason.INVALID_STATE, NOT_AIRBORNE)]
    if _battery_critical(d):
        return [nack_command(command, NackReason.BATTERY_TOO_LOW, BATTERY_CRITICAL)]
    if d.mission_active:
        return [nack_command(command, NackReason.MISSION_ACTIVE, MISSION_RUNNING)]
    if not behaviors.inside_geofence(d, point):
        return [nack_command(command, NackReason.OUT_OF_BOUNDS, TARGET_OUTSIDE)]
    behaviors.fly_to(d, Target(point, payload.altitude_m, payload.speed_mps))
    _moved_by(world, command, now)
    return [ack(command)]


@registry.handles(CommandType.RETURN_HOME)
def handle_return_home(world: World, command: Message[Any], now: float) -> list[Outgoing]:
    d = _drone(world, command)
    if not d.airborne:
        return [nack_command(command, NackReason.INVALID_STATE, NOT_AIRBORNE)]
    final_status = behaviors.abort_mission(world, d, now)
    behaviors.return_home(d)
    _moved_by(world, command, now)
    return [ack(command), *final_status]


@registry.handles(CommandType.LAND)
def handle_land(world: World, command: Message[Any], now: float) -> list[Outgoing]:
    d = _drone(world, command)
    if not d.airborne:
        return [nack_command(command, NackReason.INVALID_STATE, NOT_AIRBORNE)]
    final_status = behaviors.abort_mission(world, d, now)
    behaviors.land_in_place(d)
    _moved_by(world, command, now)
    return [ack(command), *final_status]


@registry.handles(CommandType.SET_SPEED)
def handle_set_speed(world: World, command: Message[Any], now: float) -> list[Outgoing]:
    d = _drone(world, command)
    payload: SetSpeedPayload = command.payload
    d.cruise_mps = payload.speed_mps
    return [ack(command)]


@registry.handles(CommandType.START_MISSION)
def handle_start_mission(world: World, command: Message[Any], now: float) -> list[Outgoing]:
    d = _drone(world, command)
    payload: StartMissionPayload = command.payload
    waypoints = tuple(Target(GeoPoint(wp.lat, wp.lon), wp.altitude_m) for wp in payload.waypoints)
    if not (d.state is FlightState.ARMED or d.airborne):
        return [nack_command(command, NackReason.INVALID_STATE, NOT_ARMED_OR_AIRBORNE)]
    if _battery_critical(d):
        return [nack_command(command, NackReason.BATTERY_TOO_LOW, BATTERY_CRITICAL)]
    if d.mission_active:
        return [nack_command(command, NackReason.MISSION_ACTIVE, MISSION_RUNNING)]
    if not all(behaviors.inside_geofence(d, wp.point) for wp in waypoints):
        return [nack_command(command, NackReason.OUT_OF_BOUNDS, WAYPOINT_OUTSIDE)]
    behaviors.start_mission(d, payload.mission_id, waypoints)
    _moved_by(world, command, now)
    world.upsert(behaviors.mission_job(d.drone_id, now, command.correlation_id))
    return [ack(command)]


def _drone(world: World, command: Message[Any]) -> Drone:
    """The command's drone. `dispatch` only calls a handler for a drone on the board."""
    d = world.drone(command.drone_id)
    if d is None:
        raise LookupError("handler called for a drone that is not on the board")
    return d


def _moved_by(world: World, command: Message[Any], now: float) -> None:
    """Start the POSITION job, or give the running one this command's correlation_id.
    Same period, so a running job keeps its next due time."""
    world.upsert(behaviors.position_job(command.drone_id, now, command.correlation_id))


def _battery_critical(d: Drone) -> bool:
    """At or below the critical level a drone only returns home or lands: GOTO and
    START_MISSION are refused, so a dispatcher can't redirect a drone that is going home
    on its last charge (or one that is forced to land at 0 %)."""
    return d.battery_pct <= behaviors.CRITICAL_BATTERY_PCT
