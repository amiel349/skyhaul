"""What a drone does on its own: the periodic jobs, flight progress and alerts.

Four message patterns (design note section 2):
- drone-initiated periodic: HEARTBEAT every 1 s and BATTERY every 5 s from spawn, with no
  correlation_id. The BATTERY job's tick drains the battery and checks the alerts;
- moving: POSITION every 1 s while airborne. Its tick moves the drone, under the board
  lock, so a new GOTO and a movement step can never overwrite each other. One final
  POSITION on touchdown, then the job is removed;
- triggered periodic: MISSION_STATUS every 2 s while the mission is ACTIVE, then one final
  persistent COMPLETED or ABORTED;
- event: ALERTs, latched per threshold, from `check_alerts`.

Moving and triggered telemetry carry the correlation_id of the command that started or
last changed the job (it is on the Job); everything else carries null.
"""

import random
from typing import Final

from skyhaul_contracts import (
    AlertCode,
    AlertSeverity,
    FlightState,
    MessageType,
    MissionState,
    TelemetryType,
)
from skyhaul_contracts.v1 import (
    AlertPayload,
    BatteryPayload,
    HeartbeatPayload,
    MissionStatusPayload,
    PositionPayload,
)
from skyhaul_contracts.v1 import GeoPoint as WireGeoPoint

from skyhaul_drone_sim import physics
from skyhaul_drone_sim.board import Job, World, render_job
from skyhaul_drone_sim.model import Drone, GeoPoint, Mission, Target, seed_from
from skyhaul_drone_sim.outbox import Outgoing, new_message

HEARTBEAT_PERIOD_S: Final = 1.0
BATTERY_PERIOD_S: Final = 5.0
POSITION_PERIOD_S: Final = 1.0
MISSION_STATUS_PERIOD_S: Final = 2.0

LOW_BATTERY_PCT: Final = 20.0
CRITICAL_BATTERY_PCT: Final = 10.0
MAX_REPORTED_ALTITUDE_M: Final = 150.0
MAX_REPORTED_SPEED_MPS: Final = 25.0


# --- spawn -----------------------------------------------------------------------------


def spawn_drone(
    drone_id: str,
    *,
    home: GeoPoint,
    now: float,
    seed: int,
    geofence_radius_m: float,
    battery_pct: float = 100.0,
) -> tuple[Drone, list[Job]]:
    """A landed drone at `home` and its two drone-initiated jobs."""
    drone = Drone(
        drone_id=drone_id,
        home=home,
        spawned_at=now,
        rng=random.Random(seed_from(seed, drone_id)),  # noqa: S311 - simulation, not security
        geofence_radius_m=geofence_radius_m,
        pos=home,
        battery_pct=battery_pct,
    )
    return drone, [heartbeat_job(drone_id, now), battery_job(drone_id, now)]


def add_drone(world: World, drone: Drone, jobs: list[Job]) -> None:
    world.add_drone(drone)
    for job in jobs:
        world.upsert(job)


# --- job factories ---------------------------------------------------------------------


def heartbeat_job(drone_id: str, spawned_at: float) -> Job:
    return Job(
        drone_id,
        TelemetryType.HEARTBEAT,
        HEARTBEAT_PERIOD_S,
        spawned_at + HEARTBEAT_PERIOD_S,
        render=render_heartbeat,
    )


def battery_job(drone_id: str, spawned_at: float) -> Job:
    return Job(
        drone_id,
        TelemetryType.BATTERY,
        BATTERY_PERIOD_S,
        spawned_at + BATTERY_PERIOD_S,
        render=render_battery,
        tick=battery_tick,
    )


def position_job(drone_id: str, now: float, correlation_id: str | None) -> Job:
    return Job(
        drone_id,
        TelemetryType.POSITION,
        POSITION_PERIOD_S,
        now + POSITION_PERIOD_S,
        render=render_position,
        tick=flight_tick,
        correlation_id=correlation_id,
        done=landed,
    )


def mission_job(drone_id: str, now: float, correlation_id: str | None) -> Job:
    return Job(
        drone_id,
        TelemetryType.MISSION_STATUS,
        MISSION_STATUS_PERIOD_S,
        now + MISSION_STATUS_PERIOD_S,
        render=render_mission_status,
        correlation_id=correlation_id,
        done=mission_over,
        final_persistent=True,
    )


def landed(d: Drone) -> bool:
    return d.state is FlightState.LANDED


def mission_over(d: Drone) -> bool:
    return not d.mission_active


# --- renders ---------------------------------------------------------------------------


def render_heartbeat(d: Drone, job: Job, now: float) -> HeartbeatPayload:
    return HeartbeatPayload(
        flight_state=d.state, armed=d.armed, uptime_s=max(0, int(now - d.spawned_at))
    )


def render_battery(d: Drone, job: Job, now: float) -> BatteryPayload:
    return BatteryPayload(
        charge_pct=float(d.battery_pct), est_remaining_s=physics.est_remaining_s(d)
    )


def render_position(d: Drone, job: Job, now: float) -> PositionPayload:
    target = None
    if d.target is not None:
        target = WireGeoPoint(lat=float(d.target.point.lat), lon=float(d.target.point.lon))
    return PositionPayload(
        lat=float(d.pos.lat),
        lon=float(d.pos.lon),
        altitude_m=_clamp(d.altitude_m, MAX_REPORTED_ALTITUDE_M),
        heading_deg=float(d.heading_deg),
        ground_speed_mps=_clamp(d.ground_speed_mps, MAX_REPORTED_SPEED_MPS),
        target=target,
    )


def render_mission_status(d: Drone, job: Job, now: float) -> MissionStatusPayload:
    mission = d.mission
    if mission is None:
        raise LookupError("MISSION_STATUS job without a mission")
    total = len(mission.waypoints)
    return MissionStatusPayload(
        mission_id=mission.mission_id,
        state=mission.state,
        waypoint_index=mission.index,
        waypoints_total=total,
        progress_pct=100.0 * mission.index / total,
    )


def _clamp(value: float, high: float) -> float:
    return float(min(max(value, 0.0), high))


# --- flight ----------------------------------------------------------------------------


def flight_tick(d: Drone, job: Job, dt: float) -> list[Outgoing]:
    """The POSITION job's tick: one movement step, then what arriving means."""
    if physics.step_movement(d, dt):
        _arrived(d)
    return []


def _arrived(d: Drone) -> None:
    match d.state:
        case FlightState.TAKING_OFF:
            hover(d)
        case FlightState.FLYING if d.mission is not None and d.mission.active:
            _next_waypoint(d, d.mission)
        case FlightState.FLYING:
            hover(d)
        case FlightState.RETURNING:
            land_in_place(d)
        case FlightState.LANDING:
            _touch_down(d)
        case _:
            hover(d)


def _next_waypoint(d: Drone, mission: Mission) -> None:
    mission.index += 1
    if mission.index < len(mission.waypoints):
        d.target = mission.waypoints[mission.index]
        return
    mission.state = MissionState.COMPLETED
    hover(d)


def _touch_down(d: Drone) -> None:
    d.state = FlightState.LANDED  # landing disarms
    d.altitude_m = 0.0
    d.ground_speed_mps = 0.0
    d.climbing = False
    d.target = None


def hover(d: Drone) -> None:
    d.state = FlightState.HOVERING
    d.target = None
    d.ground_speed_mps = 0.0


def fly_to(d: Drone, target: Target, state: FlightState = FlightState.FLYING) -> None:
    d.state = state
    d.target = target


def land_in_place(d: Drone) -> None:
    d.state = FlightState.LANDING
    d.target = Target(d.pos, 0.0)


def return_home(d: Drone) -> None:
    """Fly to the spawn point at the current altitude; on arrival, land."""
    fly_to(d, Target(d.home, max(d.altitude_m, 0.0)), FlightState.RETURNING)


def start_mission(d: Drone, mission_id: str, waypoints: tuple[Target, ...]) -> None:
    d.mission = Mission(mission_id, waypoints)
    fly_to(d, waypoints[0])


def abort_mission(world: World, d: Drone, now: float) -> list[Outgoing]:
    """From a handler: end an active mission with one final persistent ABORTED status,
    carrying the START_MISSION correlation_id, and remove its job."""
    if d.mission is None or not d.mission.active:
        return []
    d.mission.state = MissionState.ABORTED
    job = world.get(d.drone_id, TelemetryType.MISSION_STATUS)
    world.remove(d.drone_id, TelemetryType.MISSION_STATUS)
    final = [] if job is None else [render_job(d, job, now, persistent=True)]
    d.mission = None
    return final


def inside_geofence(d: Drone, point: GeoPoint) -> bool:
    return physics.distance_m(d.home, point) <= d.geofence_radius_m


# --- battery and alerts ----------------------------------------------------------------


def battery_tick(d: Drone, job: Job, dt: float) -> list[Outgoing]:
    """The BATTERY job's tick: drain, then the alerts and what they trigger."""
    physics.drain_battery(d, dt)
    return check_alerts(d)


def check_alerts(d: Drone) -> list[Outgoing]:
    """Raise each alert once per crossing. A threshold re-arms once the drone is back on
    the safe side. A mission ended here gets its final status from its own job."""
    alerts: list[Outgoing] = []
    if _crossed(d, AlertCode.LOW_BATTERY, d.battery_pct <= LOW_BATTERY_PCT):
        alerts.append(_alert(d, AlertCode.LOW_BATTERY, AlertSeverity.WARNING, d.battery_pct))
    if _crossed(d, AlertCode.CRITICAL_BATTERY, d.battery_pct <= CRITICAL_BATTERY_PCT):
        alerts.append(_alert(d, AlertCode.CRITICAL_BATTERY, AlertSeverity.CRITICAL, d.battery_pct))
        if d.state not in (FlightState.RETURNING, FlightState.LANDING) and d.airborne:
            _end_mission_from_tick(d)
            return_home(d)
    if d.battery_pct <= 0.0 and d.airborne and d.state is not FlightState.LANDING:
        _end_mission_from_tick(d)
        land_in_place(d)
    outside_m = physics.distance_m(d.home, d.pos) - d.geofence_radius_m
    if _crossed(d, AlertCode.GEOFENCE_BREACH, outside_m > 0):
        alerts.append(_alert(d, AlertCode.GEOFENCE_BREACH, AlertSeverity.WARNING, outside_m))
    return alerts


def _crossed(d: Drone, code: AlertCode, condition: bool) -> bool:
    """True only on the tick where `condition` becomes true; it re-arms when false."""
    if not condition:
        d.alerts_latched.discard(code)
        return False
    if code in d.alerts_latched:
        return False
    d.alerts_latched.add(code)
    return True


def _end_mission_from_tick(d: Drone) -> None:
    if d.mission is not None and d.mission.active:
        d.mission.state = MissionState.ABORTED


_ALERT_TEXT: Final = {
    AlertCode.LOW_BATTERY: "battery low",
    AlertCode.CRITICAL_BATTERY: "battery critical",
    AlertCode.GEOFENCE_BREACH: "outside the geofence",
}


def _alert(d: Drone, code: AlertCode, severity: AlertSeverity, value: float) -> Outgoing:
    payload = AlertPayload(
        code=code, severity=severity, message=_ALERT_TEXT[code], value=round(float(value), 3)
    )
    return Outgoing(new_message(MessageType.ALERT, d.drone_id, payload, None), persistent=True)
