"""Drone-initiated periodic telemetry and events (alerts), on a FakeClock with dt = 0.1 s."""

import pytest
from sim_support.drones import HOME, RADIUS_M
from sim_support.flight import Flight, goto, mission, of_type, offset_point, reply_type
from skyhaul_contracts import FlightState, MessageType
from skyhaul_contracts.v1 import SetSpeedPayload, TakeoffPayload
from skyhaul_drone_sim.outbox import Outgoing


def alerts(messages: list[Outgoing], code: str) -> list[Outgoing]:
    return [out for out in of_type(messages, "ALERT") if out.message.payload.code == code]


def test_spawned_drone_sends_heartbeat_every_1s_and_battery_every_5s_with_null_corr_id() -> None:
    flight = Flight()

    produced = flight.run(5.0)

    heartbeats = of_type(produced, "HEARTBEAT")
    batteries = of_type(produced, "BATTERY")
    assert [h.message.payload.uptime_s for h in heartbeats] == [1, 2, 3, 4, 5]
    assert len(batteries) == 1
    assert len(produced) == 6  # nothing else
    assert {out.message.correlation_id for out in produced} == {None}
    assert not any(out.persistent for out in produced)
    assert batteries[0].message.payload.charge_pct == 100.0  # landed: no drain


def test_battery_reports_charge_and_the_estimated_airborne_time_left() -> None:
    flight = Flight()
    flight.hovering()  # about 10 s of climbing

    (battery, *_) = of_type(flight.run(5.0), "BATTERY")

    assert 98.0 < battery.message.payload.charge_pct < 100.0
    assert battery.message.payload.est_remaining_s == int(battery.message.payload.charge_pct / 0.09)


def test_battery_at_20_pct_or_below_sends_one_low_battery_warning_and_none_while_below() -> None:
    flight = Flight()
    flight.send("ARM")
    flight.set(battery_pct=20.02)  # armed: 0.01 %/s, so it crosses after about 2 s

    produced = flight.run(120.0)

    (alert,) = alerts(produced, "LOW_BATTERY")
    assert alert.message.payload.severity == "WARNING"
    assert alert.message.payload.value <= 20.0
    assert alert.message.correlation_id is None
    assert alert.persistent


def test_battery_at_10_pct_or_below_sends_one_critical_alert_and_the_drone_returns_home() -> None:
    flight = Flight()
    flight.hovering()
    flight.send("GOTO", goto(offset_point(north_m=300.0)))
    flight.run_until(FlightState.HOVERING)
    flight.set(battery_pct=10.01)

    produced = flight.run(1.0)

    (alert,) = alerts(produced, "CRITICAL_BATTERY")
    assert alert.message.payload.severity == "CRITICAL"
    assert flight.drone.state is FlightState.RETURNING
    flight.run_until(FlightState.LANDED)
    assert flight.drone.pos == HOME
    assert alerts(flight.sent, "CRITICAL_BATTERY") == [alert]


def test_critical_battery_during_a_mission_aborts_it_with_one_final_status() -> None:
    flight = Flight()
    flight.hovering()
    flight.send("START_MISSION", mission(offset_point(1000.0)))
    flight.run(3.0)
    flight.set(battery_pct=10.01)

    produced = flight.run(1.0)

    (final,) = of_type(produced, "MISSION_STATUS")
    assert final.message.payload.state == "ABORTED"
    assert final.persistent
    assert flight.drone.state is FlightState.RETURNING


def test_airborne_drone_whose_battery_reaches_0_lands_in_place() -> None:
    flight = Flight()
    flight.hovering()
    flight.send("GOTO", goto(offset_point(north_m=300.0)))
    flight.run_until(FlightState.HOVERING)
    flight.set(battery_pct=0.02)  # crosses 10 % right away (RETURN_HOME), then hits 0
    produced = flight.run(0.5)
    where = flight.drone.pos

    assert flight.drone.battery_pct == 0.0
    assert flight.drone.state is FlightState.LANDING
    flight.run_until(FlightState.LANDED)
    assert flight.drone.pos == where
    assert alerts(produced, "CRITICAL_BATTERY")


def test_position_leaving_the_geofence_sends_one_breach_alert_and_a_new_one_after_return() -> None:
    flight = Flight()
    flight.hovering()
    outside, inside = offset_point(north_m=RADIUS_M + 50.0), offset_point(north_m=RADIUS_M - 50.0)

    flight.set(pos=outside)
    first = flight.run(5.0)
    flight.set(pos=inside)
    back = flight.run(5.0)
    flight.set(pos=outside)
    second = flight.run(5.0)

    (breach,) = alerts(first, "GEOFENCE_BREACH")
    assert breach.message.payload.value == pytest.approx(50.0, abs=0.01)  # meters outside
    assert alerts(back, "GEOFENCE_BREACH") == []
    assert len(alerts(second, "GEOFENCE_BREACH")) == 1


def test_battery_crossing_a_threshold_in_the_tick_right_after_a_command_applies_both() -> None:
    flight = Flight()
    flight.hovering()
    flight.set(battery_pct=20.001)

    result = flight.send("GOTO", goto(offset_point(north_m=500.0)))
    produced = flight.run(0.1)

    assert reply_type(result) is MessageType.ACK
    assert flight.drone.state is FlightState.FLYING
    assert len(alerts(produced, "LOW_BATTERY")) == 1


def run_script() -> list[dict[str, object]]:
    flight = Flight()
    flight.send("ARM")
    flight.send("SET_SPEED", SetSpeedPayload(speed_mps=7.0))
    flight.send("TAKEOFF", TakeoffPayload(altitude_m=25.0))
    flight.run(12.0)
    flight.send("GOTO", goto(offset_point(north_m=150.0, east_m=-40.0), altitude_m=40.0))
    flight.run(30.0)
    return [  # telemetry only: replies quote the command's random msg_id
        {"type": out.message.type, **out.message.payload.model_dump(mode="json")}
        for out in flight.sent
        if out.message.type not in (MessageType.ACK, MessageType.NACK)
    ]


def test_same_seed_and_same_commands_give_the_same_telemetry_values() -> None:
    assert run_script() == run_script()
