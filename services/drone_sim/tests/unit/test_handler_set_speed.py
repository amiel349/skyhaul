"""SET_SPEED: simple reply, any state. Sets the cruise speed."""

import pytest
from sim_support.flight import Flight, goto, of_type, offset_point, reply_type
from skyhaul_contracts import MessageType
from skyhaul_contracts.v1 import SetSpeedPayload


def test_spawned_drone_acks_set_speed_and_later_position_shows_the_new_cruise_speed() -> None:
    flight = Flight()

    result = flight.send("SET_SPEED", SetSpeedPayload(speed_mps=5.0))  # landed: any state
    flight.hovering()
    flight.send("GOTO", goto(offset_point(north_m=1000.0)))
    positions = of_type(flight.run(3.0), "POSITION")

    assert reply_type(result) is MessageType.ACK
    assert [p.message.payload.ground_speed_mps for p in positions] == pytest.approx([5.0] * 3)


def test_set_speed_while_flying_changes_the_speed_of_the_current_leg() -> None:
    flight = Flight()
    flight.hovering()
    flight.send("GOTO", goto(offset_point(north_m=1000.0)))
    before = of_type(flight.run(2.0), "POSITION")

    flight.send("SET_SPEED", SetSpeedPayload(speed_mps=15.0))
    after = of_type(flight.run(2.0), "POSITION")

    assert before[-1].message.payload.ground_speed_mps == pytest.approx(10.0)
    assert after[-1].message.payload.ground_speed_mps == pytest.approx(15.0)
