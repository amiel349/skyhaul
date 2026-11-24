"""GOTO: moving. Airborne, no active mission, target inside the geofence."""

from itertools import pairwise

import pytest
from sim_support.drones import HOME, RADIUS_M
from sim_support.flight import Flight, goto, nack_reason, of_type, offset_point, reply_type
from skyhaul_contracts import FlightState, MessageType
from skyhaul_contracts.v1 import TakeoffPayload
from skyhaul_drone_sim.physics import ARRIVAL_M, distance_m


def test_drone_that_is_not_airborne_gets_nack_invalid_state_for_goto() -> None:
    flight = Flight()

    assert nack_reason(flight.send("GOTO", goto(offset_point(100)))) == "INVALID_STATE"
    flight.send("ARM")
    assert nack_reason(flight.send("GOTO", goto(offset_point(100)))) == "INVALID_STATE"


@pytest.mark.parametrize(("speed_mps", "expected_mps"), [(None, 10.0), (4.0, 4.0)])
def test_hovering_drone_moves_each_tick_at_its_speed_and_stops_at_target_without_overshoot(
    speed_mps: float | None, expected_mps: float
) -> None:
    flight = Flight()
    flight.hovering()
    target = offset_point(north_m=95.0, east_m=20.0)
    total_m = distance_m(HOME, target)

    assert reply_type(flight.send("GOTO", goto(target, speed_mps=speed_mps))) is MessageType.ACK
    left, along = [total_m], [0.0]
    while flight.drone.state is FlightState.FLYING:
        flight.run(0.1)
        left.append(distance_m(flight.drone.pos, target))
        along.append(distance_m(HOME, flight.drone.pos))

    steps = [before - after for before, after in pairwise(left)]
    assert steps[:-1] == pytest.approx([expected_mps * 0.1] * (len(steps) - 1))
    # The last step snaps the drone onto the target from within the arrival tolerance.
    assert 0 < steps[-1] <= expected_mps * 0.1 + ARRIVAL_M
    assert flight.drone.pos == target
    assert along == sorted(along)
    assert max(along) == pytest.approx(total_m)  # never past the target
    assert flight.drone.state is FlightState.HOVERING


def test_goto_target_outside_the_geofence_gets_nack_out_of_bounds_and_drone_does_not_move() -> None:
    flight = Flight()
    flight.hovering()
    before = flight.drone.pos

    result = flight.send("GOTO", goto(offset_point(north_m=RADIUS_M + 10)))
    flight.run(3.0)

    assert nack_reason(result) == "OUT_OF_BOUNDS"
    assert flight.drone.pos == before
    assert flight.drone.state is FlightState.HOVERING


def test_position_carries_the_correlation_id_of_the_command_that_last_changed_movement() -> None:
    takeoff_corr, goto_a_corr, goto_b_corr = (
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
    )
    flight = Flight()
    flight.send("ARM")
    flight.send("TAKEOFF", TakeoffPayload(altitude_m=30.0), correlation_id=takeoff_corr)
    climbing = of_type(flight.run(3.0), "POSITION")
    flight.send("GOTO", goto(offset_point(500)), correlation_id=goto_a_corr)
    to_a = of_type(flight.run(3.0), "POSITION")
    flight.send("GOTO", goto(offset_point(-500)), correlation_id=goto_b_corr)
    to_b = of_type(flight.run(3.0), "POSITION")

    assert {out.message.correlation_id for out in climbing} == {takeoff_corr}
    assert {out.message.correlation_id for out in to_a} == {goto_a_corr}
    assert {out.message.correlation_id for out in to_b} == {goto_b_corr}
    assert len(climbing + to_a + to_b) == 9  # a new command does not shift the 1 s grid


def test_goto_to_the_current_position_is_acked_and_the_drone_hovers() -> None:
    flight = Flight()
    flight.hovering()

    result = flight.send("GOTO", goto(flight.drone.pos))
    flight.run(0.1)

    assert reply_type(result) is MessageType.ACK
    assert flight.drone.state is FlightState.HOVERING
    assert flight.drone.pos == HOME


@pytest.mark.parametrize(
    ("north_m", "east_m", "heading_deg"),
    [(100, 0, 0.0), (0, 100, 90.0), (-100, 0, 180.0), (0, -100, 270.0), (100, 100, 45.0)],
)
def test_position_heading_points_at_the_target(
    north_m: float, east_m: float, heading_deg: float
) -> None:
    flight = Flight()
    flight.hovering()
    flight.send("GOTO", goto(offset_point(north_m, east_m)))

    (position,) = of_type(flight.run(1.0), "POSITION")

    assert position.message.payload.heading_deg == pytest.approx(heading_deg)
    assert position.message.payload.ground_speed_mps == pytest.approx(10.0)
