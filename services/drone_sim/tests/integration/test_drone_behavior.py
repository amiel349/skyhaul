"""Drone behavior through a real RabbitMQ (issue #8): the round trip and the one real-time
cadence check. Needs Docker. Every message the client receives goes through the Codec, so
one that breaks a schema fails the test."""

from __future__ import annotations

import math
import secrets
from collections import defaultdict
from itertools import pairwise
from typing import Any

import pytest
from sim_support.command_client import CommandClient
from skyhaul_contracts import Message, MessageType
from skyhaul_contracts.v1 import ArmPayload, GotoPayload, TakeoffPayload

StartSim = Any

pytestmark = [pytest.mark.integration, pytest.mark.timeout(180)]

HOME_LAT, HOME_LON = 32.0, 34.8  # the simulator's default home
TARGET_LAT = HOME_LAT + 150.0 / 111_320.0  # 150 m north


def is_position(drone: str, check: Any) -> Any:
    def condition(message: Message[Any]) -> bool:
        return (
            message.drone_id == drone
            and message.type is MessageType.POSITION
            and bool(check(message.payload))
        )

    return condition


def test_spawned_drone_answers_arm_takeoff_goto_and_its_position_reaches_the_target(
    start_sim: StartSim, client: CommandClient, drone: str
) -> None:
    start_sim([drone])
    client.watch(drone)

    arm = client.send(MessageType.ARM, drone, ArmPayload())
    assert client.wait_reply(arm.msg_id).message.type is MessageType.ACK
    takeoff = client.send(MessageType.TAKEOFF, drone, TakeoffPayload(altitude_m=20.0))
    assert client.wait_reply(takeoff.msg_id).message.type is MessageType.ACK
    client.wait_for(is_position(drone, lambda p: p.altitude_m == 20.0))
    goto = client.send(
        MessageType.GOTO,
        drone,
        GotoPayload(lat=TARGET_LAT, lon=HOME_LON, altitude_m=20.0, speed_mps=15.0),
    )
    goto_reply = client.wait_reply(goto.msg_id)
    arrived = client.wait_for(
        is_position(drone, lambda p: math.isclose(p.lat, TARGET_LAT, abs_tol=1e-9))
    )

    assert goto_reply.message.type is MessageType.ACK
    assert goto_reply.message.correlation_id == goto.correlation_id
    assert arrived.correlation_id == goto.correlation_id
    assert arrived.payload.lon == pytest.approx(HOME_LON)
    types = {item.message.type for item in client.received}
    assert {MessageType.ACK, MessageType.HEARTBEAT, MessageType.POSITION} <= types
    assert all(item.message.drone_id == drone for item in client.received)


def test_200_drones_send_heartbeats_1s_plus_or_minus_250ms_apart_over_10s_real_time(
    start_sim: StartSim, client: CommandClient
) -> None:
    prefix = f"it{secrets.token_hex(4)}"
    drones = [f"{prefix}-d{n:03}" for n in range(1, 201)]
    for drone in drones:
        client.watch(drone)
    start_sim(drones)
    client.pump(1.5)  # past the first heartbeat of every drone

    start = len(client.received)
    client.pump(10.0)

    arrivals: dict[str, list[float]] = defaultdict(list)
    for item in client.received[start:]:
        if item.message.type is MessageType.HEARTBEAT:
            assert item.message.correlation_id is None
            arrivals[item.message.drone_id].append(item.arrived_at)
    assert set(arrivals) == set(drones)
    gaps = [b - a for times in arrivals.values() for a, b in pairwise(times)]
    assert all(len(times) >= 9 for times in arrivals.values())
    assert min(gaps) >= 0.75, min(gaps)
    assert max(gaps) <= 1.25, max(gaps)
