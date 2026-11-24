"""Straight-line movement and the battery model (ADR 0008). Pure functions with an explicit
`dt`, so tests on a controlled clock are exact. No I/O, no clock, no randomness."""

import math
from typing import Final

from skyhaul_contracts import FlightState

from skyhaul_drone_sim.model import Drone, GeoPoint, Target

M_PER_DEG: Final = 111_320.0
"""Meters per degree of latitude (and of longitude at the equator), flat-earth."""
CLIMB_MPS: Final = 3.0
DESCEND_MPS: Final = 2.0
ARRIVAL_M: Final = 1.0
ARRIVAL_ALT_M: Final = 0.5

ARMED_DRAIN_PCT_S: Final = 0.01
AIRBORNE_BASE_PCT_S: Final = 0.05
SPEED_DRAIN_PCT_S_PER_MPS: Final = 0.004
CLIMB_DRAIN_PCT_S: Final = 0.03


def offset_m(a: GeoPoint, b: GeoPoint) -> tuple[float, float]:
    """(north, east) meters from `a` to `b`, on a flat earth around `a`."""
    north = (b.lat - a.lat) * M_PER_DEG
    east = (b.lon - a.lon) * M_PER_DEG * math.cos(math.radians(a.lat))
    return north, east


def distance_m(a: GeoPoint, b: GeoPoint) -> float:
    return math.hypot(*offset_m(a, b))


def bearing_deg(a: GeoPoint, b: GeoPoint) -> float:
    """Degrees true from `a` to `b`, in [0, 360)."""
    north, east = offset_m(a, b)
    heading = math.degrees(math.atan2(east, north)) % 360.0
    return 0.0 if heading >= 360.0 else heading  # -1e-20 % 360 == 360.0


def step_movement(d: Drone, dt: float) -> bool:
    """Move `d` toward its target for `dt` seconds. Returns True when it arrived.

    Horizontal and vertical movement happen at the same time; neither overshoots. Within
    ARRIVAL_M horizontally and ARRIVAL_ALT_M vertically the drone snaps to the target.
    Without a target the drone hovers in place.
    """
    target = d.target
    if target is None:
        d.ground_speed_mps = 0.0
        d.climbing = False
        return False
    moved = _step_horizontal(d, target, dt)
    climbed = _step_vertical(d, target, dt)
    d.climbing = climbed > 0
    if dt > 0:
        d.ground_speed_mps = moved / dt
    arrived = (
        distance_m(d.pos, target.point) <= ARRIVAL_M
        and abs(d.altitude_m - target.altitude_m) <= ARRIVAL_ALT_M
    )
    if arrived:
        d.pos = target.point
        d.altitude_m = target.altitude_m
    return arrived


def _step_horizontal(d: Drone, target: Target, dt: float) -> float:
    north, east = offset_m(d.pos, target.point)
    dist = math.hypot(north, east)
    if dist == 0:
        return 0.0
    speed = target.speed_mps if target.speed_mps is not None else d.cruise_mps
    step = min(speed * dt, dist)
    d.heading_deg = bearing_deg(d.pos, target.point)
    if step == dist:
        d.pos = target.point
        return step
    frac = step / dist
    d.pos = GeoPoint(
        lat=d.pos.lat + north * frac / M_PER_DEG,
        lon=d.pos.lon + east * frac / (M_PER_DEG * math.cos(math.radians(d.pos.lat))),
    )
    return step


def _step_vertical(d: Drone, target: Target, dt: float) -> float:
    """Returns the meters climbed (negative when descending)."""
    gap = target.altitude_m - d.altitude_m
    rate = CLIMB_MPS if gap > 0 else DESCEND_MPS
    step = math.copysign(min(rate * dt, abs(gap)), gap)
    d.altitude_m += step
    return step


def drain_rate_pct_s(d: Drone) -> float:
    """Battery drain in percent per second for the drone's current state."""
    if d.state is FlightState.LANDED:
        return 0.0
    if d.state is FlightState.ARMED:
        return ARMED_DRAIN_PCT_S
    rate = AIRBORNE_BASE_PCT_S + SPEED_DRAIN_PCT_S_PER_MPS * d.ground_speed_mps
    return rate + (CLIMB_DRAIN_PCT_S if d.climbing else 0.0)


def drain_battery(d: Drone, dt: float) -> None:
    d.battery_pct = max(0.0, d.battery_pct - drain_rate_pct_s(d) * dt)


def est_remaining_s(d: Drone) -> int:
    """Airborne seconds left at cruise speed, level flight."""
    rate = AIRBORNE_BASE_PCT_S + SPEED_DRAIN_PCT_S_PER_MPS * d.cruise_mps
    return int(d.battery_pct / rate)
