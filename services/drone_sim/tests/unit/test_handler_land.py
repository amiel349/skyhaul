"""LAND: airborne -> LANDING -> LANDED (disarmed), in place."""

from itertools import pairwise

import pytest
from sim_support.flight import Flight, goto, nack_reason, of_type, offset_point, reply_type
from skyhaul_contracts import FlightState, MessageType
from skyhaul_drone_sim.physics import DESCEND_MPS


def test_drone_that_is_not_airborne_gets_nack_invalid_state_for_land() -> None:
    flight = Flight()

    assert nack_reason(flight.send("LAND")) == "INVALID_STATE"
    flight.send("ARM")
    assert nack_reason(flight.send("LAND")) == "INVALID_STATE"


def test_airborne_drone_descends_in_place_lands_disarms_and_sends_one_final_position() -> None:
    flight = Flight()
    flight.hovering(altitude_m=20.0)
    spot = offset_point(north_m=100.0)
    flight.send("GOTO", goto(spot, altitude_m=20.0))
    flight.run_until(FlightState.HOVERING)

    result = flight.send("LAND")
    descent = flight.run(20.0 / DESCEND_MPS)  # 10 s
    after = flight.run(5.0)

    assert reply_type(result) is MessageType.ACK
    positions = of_type(descent, "POSITION")
    assert {(p.message.payload.lat, p.message.payload.lon) for p in positions} == {
        (spot.lat, spot.lon)
    }
    altitudes = [p.message.payload.altitude_m for p in positions]
    periodic = altitudes[:-1]
    assert [b - a for a, b in pairwise(periodic)] == pytest.approx(
        [-DESCEND_MPS] * (len(periodic) - 1)  # one every 1 s, descending at 2 m/s
    )
    assert altitudes.count(0.0) == 1  # exactly one message on touchdown
    assert flight.drone.state is FlightState.LANDED
    assert flight.drone.armed is False
    assert of_type(after, "POSITION") == []


def test_land_while_flying_cancels_the_goto_and_lands_where_it_is() -> None:
    flight = Flight()
    flight.hovering()
    flight.send("GOTO", goto(offset_point(north_m=1000.0)))
    flight.run(5.0)
    where = flight.drone.pos

    flight.send("LAND")
    flight.run_until(FlightState.LANDED)

    assert flight.drone.pos == where
