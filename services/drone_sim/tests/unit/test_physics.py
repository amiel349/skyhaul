"""The movement and battery models (ADR 0008), as pure functions with an explicit dt."""

import math

import pytest
from sim_support.drones import HOME, make_drone
from sim_support.flight import offset_point
from skyhaul_contracts import FlightState
from skyhaul_drone_sim import physics
from skyhaul_drone_sim.model import GeoPoint, Target


def test_offset_distance_and_bearing_on_the_flat_earth() -> None:
    target = offset_point(north_m=300.0, east_m=400.0)

    north, east = physics.offset_m(HOME, target)

    assert (north, east) == pytest.approx((300.0, 400.0))
    assert physics.distance_m(HOME, target) == pytest.approx(500.0)
    assert physics.bearing_deg(HOME, target) == pytest.approx(math.degrees(math.atan2(400, 300)))


def test_bearing_is_always_below_360() -> None:
    just_west_of_north = GeoPoint(HOME.lat + 0.01, HOME.lon - 1e-300)

    assert 0.0 <= physics.bearing_deg(HOME, just_west_of_north) < 360.0


def test_step_never_overshoots_and_arrives_exactly_on_the_target() -> None:
    d = make_drone(state=FlightState.FLYING, altitude_m=30.0)
    d.target = Target(offset_point(north_m=25.0), 30.0)

    arrivals = [physics.step_movement(d, 0.5) for _ in range(6)]  # 5 m per step

    assert arrivals == [False, False, False, False, True, True]
    assert d.pos == d.target.point


def test_arrival_tolerance_is_1m_horizontally_and_half_a_meter_vertically() -> None:
    d = make_drone(altitude_m=29.6, pos=offset_point(north_m=-0.9))
    d.target = Target(HOME, 30.0)

    assert physics.step_movement(d, 0.0)  # no movement at all, already within tolerance
    assert (d.pos, d.altitude_m) == (HOME, 30.0)

    d.altitude_m = 29.4
    assert not physics.step_movement(d, 0.0)


def test_climb_at_3_and_descend_at_2_mps_alongside_horizontal_movement() -> None:
    up = make_drone(altitude_m=10.0)
    up.target = Target(offset_point(north_m=100.0), 40.0)
    down = make_drone(altitude_m=40.0)
    down.target = Target(offset_point(north_m=100.0), 10.0)

    physics.step_movement(up, 1.0)
    physics.step_movement(down, 1.0)

    assert (up.altitude_m, up.climbing) == (13.0, True)
    assert (down.altitude_m, down.climbing) == (38.0, False)
    assert up.ground_speed_mps == down.ground_speed_mps == pytest.approx(10.0)


def test_no_target_means_hover_with_zero_ground_speed() -> None:
    d = make_drone(ground_speed_mps=10.0, altitude_m=30.0)

    assert not physics.step_movement(d, 0.1)
    assert (d.pos, d.altitude_m, d.ground_speed_mps) == (HOME, 30.0, 0.0)


@pytest.mark.parametrize(
    ("state", "speed", "climbing", "rate"),
    [
        (FlightState.LANDED, 0.0, False, 0.0),
        (FlightState.ARMED, 0.0, False, 0.01),
        (FlightState.HOVERING, 0.0, False, 0.05),
        (FlightState.FLYING, 10.0, False, 0.09),
        (FlightState.TAKING_OFF, 0.0, True, 0.08),
        (FlightState.FLYING, 10.0, True, 0.12),
    ],
)
def test_battery_drain_rate_per_state(
    state: FlightState, speed: float, climbing: bool, rate: float
) -> None:
    d = make_drone(state=state, ground_speed_mps=speed, climbing=climbing)

    physics.drain_battery(d, 10.0)

    assert physics.drain_rate_pct_s(d) == pytest.approx(rate)
    assert d.battery_pct == pytest.approx(100.0 - 10 * rate)


def test_full_battery_lasts_about_18_minutes_at_10_mps() -> None:
    d = make_drone(battery_pct=100.0)

    assert physics.est_remaining_s(d) == pytest.approx(18.5 * 60, abs=5)


def test_battery_never_goes_below_zero() -> None:
    d = make_drone(state=FlightState.FLYING, battery_pct=0.01, ground_speed_mps=10.0)

    physics.drain_battery(d, 10.0)

    assert d.battery_pct == 0.0
    assert physics.est_remaining_s(d) == 0
