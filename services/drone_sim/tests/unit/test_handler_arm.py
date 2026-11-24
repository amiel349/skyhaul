"""ARM: simple reply. LANDED and battery >= 15 % -> ARMED."""

from sim_support.flight import Flight, nack_reason, of_type
from skyhaul_contracts import FlightState, MessageType
from skyhaul_contracts.v1 import TakeoffPayload


def test_landed_drone_with_battery_15_pct_or_more_gets_one_ack_and_heartbeat_says_armed() -> None:
    flight = Flight(battery_pct=15.0)

    result = flight.send("ARM", correlation_id="0b7d4c43-8d33-4f4e-a3e1-5b4e3c1f2a10")

    (reply,) = result.replies  # once, straight away: no tick ran
    assert reply.message.type is MessageType.ACK
    assert reply.persistent
    assert reply.message.correlation_id == "0b7d4c43-8d33-4f4e-a3e1-5b4e3c1f2a10"
    assert flight.drone.state is FlightState.ARMED
    (heartbeat,) = of_type(flight.run(1.0), "HEARTBEAT")
    assert heartbeat.message.payload.flight_state == "ARMED"
    assert heartbeat.message.payload.armed is True


def test_landed_drone_with_battery_below_15_percent_gets_nack_battery_too_low() -> None:
    flight = Flight(battery_pct=14.99)

    result = flight.send("ARM")

    assert nack_reason(result) == "BATTERY_TOO_LOW"
    assert flight.drone.state is FlightState.LANDED


def test_drone_that_is_not_landed_gets_nack_invalid_state_for_arm() -> None:
    flight = Flight()
    flight.send("ARM")

    assert nack_reason(flight.send("ARM")) == "INVALID_STATE"  # a new msg_id: not a duplicate
    flight.send("TAKEOFF", TakeoffPayload(altitude_m=30.0))
    flight.run_until(FlightState.HOVERING)
    assert nack_reason(flight.send("ARM")) == "INVALID_STATE"
    assert flight.drone.state is FlightState.HOVERING
