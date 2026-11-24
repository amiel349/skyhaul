"""The control API as seen on the wire: a real simulator subprocess, a Testcontainers
RabbitMQ, and the drones' telemetry."""

import secrets
from datetime import datetime
from itertools import pairwise
from typing import Any

import pytest
from sim_support.command_client import CommandClient
from skyhaul_contracts import MessageType

Broker = Any  # the conftest fixtures
StartSim = Any

pytestmark = pytest.mark.integration


def heartbeat_from(drone_id: str):  # type: ignore[no-untyped-def]
    return lambda m: m.drone_id == drone_id and m.type is MessageType.HEARTBEAT


def test_spawning_5_drones_gives_ids_d001_to_d005_and_they_send_heartbeats(
    start_sim: StartSim, client: CommandClient
) -> None:
    sim = start_sim([])
    namespace = f"w{secrets.token_hex(4)}"

    ids = sim.control.spawn(namespace, 5)

    assert ids == [f"{namespace}-d{n:03}" for n in range(1, 6)]
    for drone_id in ids:
        client.watch(drone_id)
    for drone_id in ids:
        client.wait_for(heartbeat_from(drone_id), timeout_s=10)


def test_reset_isolates_namespaces_and_nothing_from_before_it_is_published_after(
    start_sim: StartSim, client: CommandClient, broker: Broker
) -> None:
    sim = start_sim([])
    w1, w2 = f"a{secrets.token_hex(4)}", f"b{secrets.token_hex(4)}"
    w1_ids, w2_ids = sim.control.spawn(w1, 3), sim.control.spawn(w2, 2)
    for drone_id in w1_ids + w2_ids:
        client.watch(drone_id)
    for drone_id in w1_ids + w2_ids:
        client.wait_for(heartbeat_from(drone_id), timeout_s=10)

    result = sim.control.reset(w1)

    # A queue bound only after the reset returned sees only what is published after it.
    with CommandClient(broker.params) as after:
        for drone_id in w1_ids + w2_ids:
            after.watch(drone_id)
        after.pump(3.0)
        client.pump(1.0)

    assert result == {"namespace": w1, "drones_removed": 3, "jobs_removed": 6, "epoch": 1}
    assert {m.message.drone_id for m in after.received} == set(w2_ids)  # w2 keeps sending
    assert sim.control.call("GET", f"/drones?namespace={w1}").body == {"drones": []}
    for drone_id in w2_ids:  # without a gap across the reset
        beats = [
            datetime.fromisoformat(m.message.ts)
            for m in client.received
            if m.message.drone_id == drone_id and m.message.type is MessageType.HEARTBEAT
        ]
        gaps = [(b - a).total_seconds() for a, b in pairwise(beats)]
        assert gaps
        assert max(gaps) < 1.5
    assert sim.control.stats()["dropped_stale"] >= 0
