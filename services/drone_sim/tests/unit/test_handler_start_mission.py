"""START_MISSION: triggered periodic. ARMED or airborne, no active mission, every waypoint
inside the geofence. MISSION_STATUS every 2 s while ACTIVE, then one final status."""

import pytest
from sim_support.drones import RADIUS_M
from sim_support.flight import (
    Flight,
    goto,
    mission,
    nack_reason,
    of_type,
    offset_point,
    reply_type,
)
from skyhaul_contracts import FlightState, MessageType, TelemetryType

CORR = "55555555-5555-4555-8555-555555555555"
WAYPOINTS = (offset_point(100.0), offset_point(100.0, 100.0), offset_point(0.0, 100.0))


def flying_mission(start: str) -> Flight:
    flight = Flight()
    if start == "hovering":
        flight.hovering()
    else:
        flight.send("ARM")
    result = flight.send("START_MISSION", mission(*WAYPOINTS), correlation_id=CORR)
    assert reply_type(result) is MessageType.ACK
    return flight


@pytest.mark.parametrize("start", ["armed", "hovering"])
def test_armed_or_airborne_drone_flies_waypoints_in_order_with_mission_status_active_every_2s(
    start: str,
) -> None:
    flight = flying_mission(start)
    reached: list[int] = []
    statuses = []
    for _ in range(250):  # 25 s: past the second waypoint, not yet at the third
        statuses += of_type(flight.run(0.1), "MISSION_STATUS")
        drone = flight.drone
        assert drone.mission is not None
        if drone.pos in WAYPOINTS and WAYPOINTS.index(drone.pos) not in reached:
            reached.append(WAYPOINTS.index(drone.pos))

    assert reached == [0, 1]
    assert [s.message.payload.state for s in statuses] == ["ACTIVE"] * 12  # t = 2, 4, .. 24
    assert {s.message.correlation_id for s in statuses} == {CORR}
    assert [s.message.payload.waypoint_index for s in statuses] == sorted(
        s.message.payload.waypoint_index for s in statuses
    )
    assert {s.message.payload.waypoints_total for s in statuses} == {3}


def test_active_mission_refuses_a_second_start_mission_and_a_goto_with_mission_active() -> None:
    flight = flying_mission("hovering")
    flight.run(1.0)

    second = flight.send("START_MISSION", mission(offset_point(10.0), mission_id="m2"))
    goto_result = flight.send("GOTO", goto(offset_point(-10.0)))

    assert nack_reason(second) == "MISSION_ACTIVE"
    assert nack_reason(goto_result) == "MISSION_ACTIVE"
    assert flight.drone.mission is not None
    assert flight.drone.mission.mission_id == "m1"


def test_last_waypoint_reached_sends_one_final_completed_hovers_there_and_no_more_status() -> None:
    flight = flying_mission("hovering")

    produced = flight.run(60.0)

    statuses = of_type(produced, "MISSION_STATUS")
    *active, final = statuses
    assert {s.message.payload.state for s in active} == {"ACTIVE"}
    assert final.message.payload.state == "COMPLETED"
    assert final.message.payload.progress_pct == 100.0
    assert final.persistent
    assert final.message.correlation_id == CORR
    assert flight.drone.state is FlightState.HOVERING
    assert flight.drone.pos == WAYPOINTS[-1]
    assert produced.index(final) > produced.index(active[-1])
    assert of_type(flight.run(10.0), "MISSION_STATUS") == []
    assert flight.board.snapshot()  # the drone's own jobs are still there
    assert TelemetryType.MISSION_STATUS not in {job["type"] for job in flight.board.snapshot()}


@pytest.mark.parametrize("abort", ["LAND", "RETURN_HOME"])
def test_land_or_return_home_during_a_mission_sends_one_final_aborted_and_no_more_follow(
    abort: str,
) -> None:
    flight = flying_mission("hovering")
    flight.run(3.0)

    result = flight.send(abort)
    later = flight.run(30.0)

    ack, final = result.replies
    assert ack.message.type is MessageType.ACK
    assert final.message.type is MessageType.MISSION_STATUS
    assert final.message.payload.state == "ABORTED"
    assert final.message.correlation_id == CORR
    assert final.persistent
    assert of_type(later, "MISSION_STATUS") == []
    assert flight.drone.mission is None


def test_waypoint_outside_the_geofence_gets_nack_out_of_bounds_and_no_mission_starts() -> None:
    flight = Flight()
    flight.hovering()
    before = flight.drone.pos

    result = flight.send(
        "START_MISSION", mission(offset_point(100.0), offset_point(RADIUS_M + 1.0))
    )
    produced = flight.run(5.0)

    assert nack_reason(result) == "OUT_OF_BOUNDS"
    assert flight.drone.mission is None
    assert flight.drone.pos == before
    assert of_type(produced, "MISSION_STATUS") == []


def test_landed_drone_gets_invalid_state_for_start_mission() -> None:
    flight = Flight()

    assert nack_reason(flight.send("START_MISSION", mission(*WAYPOINTS))) == "INVALID_STATE"
