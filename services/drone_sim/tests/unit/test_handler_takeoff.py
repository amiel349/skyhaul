"""TAKEOFF: moving. ARMED -> TAKING_OFF -> HOVERING, starts the POSITION job."""

import pytest
from sim_support.drones import HOME
from sim_support.flight import Flight, nack_reason, of_type, reply_type
from skyhaul_contracts import FlightState, MessageType
from skyhaul_contracts.v1 import TakeoffPayload
from skyhaul_drone_sim.physics import CLIMB_MPS


def test_drone_that_is_not_armed_gets_nack_invalid_state_for_takeoff() -> None:
    flight = Flight()

    assert nack_reason(flight.send("TAKEOFF", TakeoffPayload(altitude_m=30.0))) == "INVALID_STATE"
    flight.hovering()
    assert nack_reason(flight.send("TAKEOFF", TakeoffPayload(altitude_m=50.0))) == "INVALID_STATE"
    assert flight.drone.altitude_m == 30.0


def test_armed_drone_climbs_to_the_commanded_altitude_and_sends_position_every_1s() -> None:
    flight = Flight()
    flight.send("ARM")

    result = flight.send("TAKEOFF", TakeoffPayload(altitude_m=30.0))
    positions = of_type(flight.run(15.0), "POSITION")

    assert reply_type(result) is MessageType.ACK
    assert len(positions) == 15  # t = 1 .. 15 s, every 1 s while airborne
    altitudes = [out.message.payload.altitude_m for out in positions]
    assert altitudes[0] == pytest.approx(CLIMB_MPS * 1.0)
    assert altitudes == sorted(altitudes)
    assert altitudes[-1] == 30.0
    assert {(out.message.payload.lat, out.message.payload.lon) for out in positions} == {
        (HOME.lat, HOME.lon)  # vertical only
    }
    assert flight.drone.state is FlightState.HOVERING
    assert positions[-1].message.payload.target is None


def test_drone_is_taking_off_until_it_reaches_the_altitude() -> None:
    flight = Flight()
    flight.send("ARM")
    flight.send("TAKEOFF", TakeoffPayload(altitude_m=30.0))

    flight.run(9.0)  # 27 m

    assert flight.drone.state is FlightState.TAKING_OFF
    flight.run(1.0)
    assert flight.drone.state is FlightState.HOVERING


@pytest.mark.parametrize("battery_pct", [10.0, 0.0])
def test_armed_drone_with_battery_at_or_below_10_pct_gets_nack_battery_too_low_for_takeoff(
    battery_pct: float,
) -> None:
    flight = Flight()
    flight.send("ARM")
    flight.set(battery_pct=battery_pct)

    assert nack_reason(flight.send("TAKEOFF", TakeoffPayload(altitude_m=30.0))) == "BATTERY_TOO_LOW"
    assert flight.drone.state is FlightState.ARMED
