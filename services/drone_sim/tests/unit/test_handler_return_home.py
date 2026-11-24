"""RETURN_HOME: moving. Airborne -> RETURNING -> LANDING -> LANDED (disarmed)."""

from sim_support.drones import HOME
from sim_support.flight import Flight, goto, nack_reason, of_type, offset_point, reply_type
from skyhaul_contracts import FlightState, MessageType

CORR = "44444444-4444-4444-8444-444444444444"


def test_drone_that_is_not_airborne_gets_nack_invalid_state_for_return_home() -> None:
    flight = Flight()

    assert nack_reason(flight.send("RETURN_HOME")) == "INVALID_STATE"
    flight.send("ARM")
    assert nack_reason(flight.send("RETURN_HOME")) == "INVALID_STATE"


def test_airborne_drone_flies_home_lands_disarms_and_sends_one_final_position_on_landing() -> None:
    flight = Flight()
    flight.hovering()
    flight.send("GOTO", goto(offset_point(north_m=200.0, east_m=-50.0)))
    flight.run_until(FlightState.HOVERING)

    result = flight.send("RETURN_HOME", correlation_id=CORR)
    states = set()
    produced = []
    while flight.drone.state is not FlightState.LANDED:
        produced += flight.run(0.1)
        states.add(flight.drone.state)
    after = flight.run(5.0)

    assert reply_type(result) is MessageType.ACK
    assert {FlightState.RETURNING, FlightState.LANDING} <= states
    drone = flight.drone
    assert (drone.pos, drone.altitude_m, drone.armed) == (HOME, 0.0, False)
    final = of_type(produced, "POSITION")[-1]
    assert final.message.payload.altitude_m == 0.0
    assert (final.message.payload.lat, final.message.payload.lon) == (HOME.lat, HOME.lon)
    assert final.message.correlation_id == CORR
    assert of_type(after, "POSITION") == []  # POSITION stops after touchdown
    (heartbeat, *_) = of_type(after, "HEARTBEAT")
    assert heartbeat.message.payload.flight_state == "LANDED"
    assert heartbeat.message.payload.armed is False
