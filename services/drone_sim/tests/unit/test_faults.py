"""Fault injection (#10), one behavior-level test per acceptance criterion: the real handlers
and board on a FakeClock with dt = 0.1 s, and the control API through the Flask test client.
No sleeps."""

import time
from typing import Any

import pytest
from flask.testing import FlaskClient
from sim_support.config import sim_config
from sim_support.drones import make_drone, spawned_board
from sim_support.flight import Flight, goto, of_type, offset_point, reply_type
from sim_support.messages import command
from skyhaul_contracts import FlightState, MessageType
from skyhaul_contracts.v1 import SetSpeedPayload
from skyhaul_drone_sim import physics
from skyhaul_drone_sim.board import MAX_DELAYED_PER_NAMESPACE, JobsBoard
from skyhaul_drone_sim.clock import FakeClock
from skyhaul_drone_sim.control_api import create_app
from skyhaul_drone_sim.faults import Fault, FaultKind
from skyhaul_drone_sim.model import GeoPoint
from skyhaul_drone_sim.outbox import Outgoing

CFG = sim_config()
AUTH = {"Authorization": f"Bearer {CFG.control_token}"}
D1, D2 = "main-d001", "main-d002"


def inject(flight: Flight, kind: FaultKind, duration_s: float | None = None, **params: Any) -> None:
    expires_at = None if duration_s is None else flight.now + duration_s
    assert flight.board.add_fault(flight.drone_id, Fault(kind, expires_at, **params))


def alerts(messages: list[Outgoing], code: str) -> list[Outgoing]:
    return [out for out in of_type(messages, "ALERT") if out.message.payload.code == code]


def flying_north(flight: Flight, north_m: float = 300.0) -> None:
    flight.hovering()
    assert reply_type(flight.send("GOTO", goto(offset_point(north_m=north_m)))) is MessageType.ACK


def api_for(board: JobsBoard, clock: FakeClock) -> FlaskClient:
    app = create_app(board, CFG, clock=clock, health=lambda: True, stats=lambda: {})
    return app.test_client()


# --- link loss -------------------------------------------------------------------------


def test_during_link_loss_the_drone_publishes_nothing_and_answers_no_command() -> None:
    flight = Flight()
    flying_north(flight)
    seen = command("SET_SPEED", D1, payload=SetSpeedPayload(speed_mps=12.0))
    assert reply_type(flight.send("SET_SPEED", msg=seen)) is MessageType.ACK
    inject(flight, FaultKind.LINK_LOSS, duration_s=5.0)

    lost = command("SET_SPEED", D1, payload=SetSpeedPayload(speed_mps=5.0))
    new = flight.send("SET_SPEED", msg=lost)
    duplicate = flight.send("SET_SPEED", msg=seen)
    produced = flight.run(4.9)

    assert new.replies == []
    assert new.link_lost
    assert duplicate.replies == []  # not even the cached ACK is replayed
    assert flight.drone.cruise_mps == 12.0  # not applied
    assert lost.msg_id not in flight.drone.recent_cmds  # not cached
    assert produced == []


def test_when_link_loss_ends_the_drone_kept_flying_and_draining_and_sends_link_restored() -> None:
    flight = Flight()
    flying_north(flight)
    before = flight.drone
    inject(flight, FaultKind.LINK_LOSS, duration_s=3.0)

    during = flight.run(2.9)
    gap = flight.drone
    after = flight.run(2.1)

    assert during == []
    assert physics.distance_m(before.pos, gap.pos) > 25.0
    assert gap.battery_pct < before.battery_pct
    (restored,) = alerts(after, "LINK_RESTORED")
    assert restored.message.payload.severity == "INFO"
    assert restored.message.correlation_id is None
    assert restored.persistent
    assert len(of_type(after, "HEARTBEAT")) == 2
    assert flight.drone.faults == {}


# --- NACK next -------------------------------------------------------------------------


def test_nack_next_rejects_the_next_n_commands_with_injected_and_then_handles_normally() -> None:
    flight = Flight()
    flight.send("ARM")
    inject(flight, FaultKind.NACK_NEXT, count=3)

    nacked = [flight.send("SET_SPEED", SetSpeedPayload(speed_mps=5.0)) for _ in range(3)]
    cruise_after_nacks = flight.drone.cruise_mps
    handled = flight.send("SET_SPEED", SetSpeedPayload(speed_mps=5.0))

    for result in nacked:
        (nack,) = result.replies
        assert nack.message.type is MessageType.NACK
        assert nack.message.payload.reason == "INJECTED"
    assert cruise_after_nacks == 10.0  # not applied
    assert reply_type(handled) is MessageType.ACK
    assert flight.drone.cruise_mps == 5.0
    assert flight.drone.faults == {}


# --- reply delay -----------------------------------------------------------------------


def test_reply_delay_publishes_the_reply_no_earlier_than_d_and_not_for_other_drones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_sleep(seconds: float) -> None:
        raise AssertionError("a handler slept")

    monkeypatch.setattr(time, "sleep", no_sleep)
    flight = Flight()
    flight.board = spawned_board(D1, D2)
    inject(flight, FaultKind.REPLY_DELAY, delay_s=1.5)
    sent_at = flight.now

    delayed = flight.send("ARM")
    other = flight.send("ARM", msg=command("ARM", D2))
    early = flight.run(1.4)
    late = flight.run(0.2)

    assert delayed.replies == []
    assert delayed.delayed
    assert flight.drone.state is FlightState.ARMED  # applied at once, only the reply waits
    assert reply_type(other) is MessageType.ACK
    assert of_type(early, "ACK") == []
    (ack,) = of_type(late, "ACK")
    assert ack.message.drone_id == D1
    assert ack.message.correlation_id is not None
    assert flight.now - sent_at >= 1.5
    assert ack.epoch == 0
    assert ack.namespace == "main"


def test_with_1000_delayed_replies_waiting_the_next_command_gets_nack_internal_error() -> None:
    flight = Flight()
    inject(flight, FaultKind.REPLY_DELAY, delay_s=30.0)
    for _ in range(MAX_DELAYED_PER_NAMESPACE):
        assert flight.send("SET_SPEED", SetSpeedPayload(speed_mps=5.0)).delayed

    overflow = flight.send("SET_SPEED", SetSpeedPayload(speed_mps=7.0))

    (nack,) = overflow.replies
    assert nack.message.type is MessageType.NACK
    assert nack.message.payload.reason == "INTERNAL_ERROR"
    assert flight.drone.cruise_mps == 5.0  # not applied


def test_deleting_a_drone_drops_its_delayed_replies_so_they_stop_counting_toward_the_cap() -> None:
    flight = Flight()
    inject(flight, FaultKind.REPLY_DELAY, delay_s=30.0)
    for _ in range(MAX_DELAYED_PER_NAMESPACE):
        assert flight.send("SET_SPEED", SetSpeedPayload(speed_mps=5.0)).delayed

    assert flight.board.remove_drone(flight.drone_id)
    flight.board.add_drone(make_drone(flight.drone_id))
    inject(flight, FaultKind.REPLY_DELAY, delay_s=30.0)

    assert flight.send("SET_SPEED", SetSpeedPayload(speed_mps=6.0)).delayed


# --- battery drain and GPS drift -------------------------------------------------------


def test_battery_drain_k_drains_k_times_faster_and_low_battery_fires_earlier() -> None:
    normal, drained = Flight(), Flight()
    for flight in (normal, drained):
        flight.hovering()
        flight.set(battery_pct=20.5)
    inject(drained, FaultKind.BATTERY_DRAIN, factor=10.0)

    normal_out, drained_out = normal.run(2.0), drained.run(2.0)

    normal_drop = 20.5 - normal.drone.battery_pct
    drained_drop = 20.5 - drained.drone.battery_pct
    assert drained_drop == pytest.approx(10 * normal_drop)
    assert alerts(drained_out, "LOW_BATTERY")
    assert not alerts(normal_out, "LOW_BATTERY")


def test_gps_drift_offsets_only_the_reported_position_and_arrival_uses_the_true_one() -> None:
    flight = Flight()
    target = offset_point(north_m=200.0)
    flight.hovering()
    flight.send("GOTO", goto(target))
    inject(flight, FaultKind.GPS_DRIFT, drift_m=50.0)

    errors: list[float] = []
    while flight.drone.state is not FlightState.HOVERING:
        for position in of_type(flight.run(0.1), "POSITION"):
            reported = GeoPoint(position.message.payload.lat, position.message.payload.lon)
            errors.append(physics.distance_m(flight.drone.pos, reported))

    assert len(errors) > 10
    assert max(errors) <= 50.0 + 1e-6
    assert max(errors) > 5.0  # the drift is really applied
    assert flight.drone.pos == target  # arrival snapped to the true target


# --- lifetime and errors ---------------------------------------------------------------


def test_a_fault_with_duration_s_is_gone_after_it_and_the_drone_behaves_normally() -> None:
    flight = Flight()
    flight.hovering()
    inject(flight, FaultKind.BATTERY_DRAIN, duration_s=2.0, factor=10.0)
    flight.run(2.5)

    before = flight.drone.battery_pct
    flight.run(1.0)

    assert flight.drone.faults == {}
    assert before - flight.drone.battery_pct == pytest.approx(0.05)  # hovering, factor 1


def test_clearing_faults_gives_204_and_normal_behavior_from_the_next_tick() -> None:
    flight = Flight()
    api = api_for(flight.board, flight.clock)
    posted = api.post(
        f"/drones/{D1}/faults", json={"kind": "LINK_LOSS", "duration_s": 3}, headers=AUTH
    )
    assert posted.status_code == 201
    silent = flight.run(1.0)

    cleared = api.delete(f"/drones/{D1}/faults", headers=AUTH)
    after = flight.run(3.0)  # past the original expiry

    assert silent == []
    assert cleared.status_code == 204
    assert len(of_type(after, "HEARTBEAT")) == 3
    assert alerts(after, "LINK_RESTORED") == []  # cleared, not expired
    assert reply_type(flight.send("ARM")) is MessageType.ACK


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "NOPE", "duration_s": 5},
        {"duration_s": 5},
        {"kind": "LINK_LOSS"},
        {"kind": "LINK_LOSS", "duration_s": 0.5},
        {"kind": "LINK_LOSS", "duration_s": 601},
        {"kind": "NACK_NEXT", "count": 0},
        {"kind": "NACK_NEXT", "count": 101},
        {"kind": "REPLY_DELAY", "delay_s": 0.05},
        {"kind": "REPLY_DELAY", "delay_s": 31},
        {"kind": "BATTERY_DRAIN", "factor": 0.5},
        {"kind": "BATTERY_DRAIN", "factor": 101},
        {"kind": "GPS_DRIFT", "drift_m": 0.5},
        {"kind": "GPS_DRIFT", "drift_m": 501},
        {"kind": "NACK_NEXT", "count": 3, "delay_s": 1},
    ],
)
def test_an_unknown_kind_or_a_parameter_out_of_range_gets_400(body: dict[str, Any]) -> None:
    flight = Flight()
    api = api_for(flight.board, flight.clock)

    reply = api.post(f"/drones/{D1}/faults", json=body, headers=AUTH)

    assert reply.status_code == 400
    assert reply.json is not None
    assert "NOPE" not in reply.json["detail"]
    assert flight.drone.faults == {}


def test_an_unknown_drone_gets_404_and_no_token_gets_401() -> None:
    flight = Flight()
    api = api_for(flight.board, flight.clock)
    body = {"kind": "NACK_NEXT", "count": 1}

    assert api.post("/drones/main-d999/faults", json=body, headers=AUTH).status_code == 404
    assert api.delete("/drones/main-d999/faults", headers=AUTH).status_code == 404
    assert api.post(f"/drones/{D1}/faults", json=body).status_code == 401
    assert api.delete(f"/drones/{D1}/faults").status_code == 401
    assert flight.drone.faults == {}


def test_a_fault_on_w1_d001_shows_on_that_drone_only() -> None:
    clock = FakeClock()
    api = api_for(JobsBoard(), clock)
    api.post("/drones", json={"namespace": "w1", "count": 2}, headers=AUTH)

    posted = api.post(
        "/drones/w1-d001/faults",
        json={"kind": "GPS_DRIFT", "drift_m": 20, "duration_s": 60},
        headers=AUTH,
    )
    clock.advance(10.0)
    listed = api.get("/drones?namespace=w1", headers=AUTH).json
    detail = api.get("/drones/w1-d001", headers=AUTH).json

    assert posted.status_code == 201
    assert posted.json == {"fault": {"kind": "GPS_DRIFT", "drift_m": 20, "expires_in_s": 60.0}}
    assert listed is not None
    assert detail is not None
    faults = {drone["drone_id"]: drone["faults"] for drone in listed["drones"]}
    shown = [{"kind": "GPS_DRIFT", "drift_m": 20, "expires_in_s": 50.0}]
    assert faults == {"w1-d001": shown, "w1-d002": []}
    assert detail["faults"] == shown


# --- reset (#9 open items) -------------------------------------------------------------


def test_reset_drops_faults_and_delayed_replies_and_sends_no_link_restored() -> None:
    flight = Flight()
    flight.board = spawned_board(D1, D2)
    inject(flight, FaultKind.REPLY_DELAY, delay_s=1.0)
    assert flight.send("ARM").delayed
    assert flight.board.add_fault(D2, Fault(FaultKind.LINK_LOSS, flight.now + 1.0))

    flight.board.reset("main")
    produced = flight.run(2.0)

    assert produced == []
