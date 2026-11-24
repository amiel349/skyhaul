"""Fault injection on the wire (#10): a real simulator subprocess, a Testcontainers RabbitMQ.
LINK_LOSS silences the drone and acks its commands; REPLY_DELAY holds one drone's reply."""

import secrets
import time
from typing import Any

import pytest
from sim_support.command_client import CommandClient
from skyhaul_contracts import Message, MessageType
from skyhaul_contracts.topology import SIM_COMMAND_QUEUE
from skyhaul_contracts.v1 import ArmPayload

Broker = Any  # the conftest fixtures
StartSim = Any

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]

DROPPED_LOG = "command dropped: the drone is in an injected link loss"


def queued(broker: Broker) -> int:
    queue = broker.queue(SIM_COMMAND_QUEUE)
    return int(queue.get("messages_ready", 0)) + int(queue.get("messages_unacknowledged", 0))


def from_drone(drone_id: str, type: MessageType) -> Any:
    def condition(message: Message[Any]) -> bool:
        return message.drone_id == drone_id and message.type is type

    return condition


def test_link_loss_silences_the_drone_then_link_restored_and_heartbeats_resume(
    start_sim: StartSim, client: CommandClient, broker: Broker
) -> None:
    sim = start_sim([])
    (drone,) = sim.control.spawn(f"f{secrets.token_hex(4)}", 1)
    client.watch(drone)
    client.wait_for(from_drone(drone, MessageType.HEARTBEAT), timeout_s=10)

    fault = sim.control.call(
        "POST", f"/drones/{drone}/faults", {"kind": "LINK_LOSS", "duration_s": 3}
    )
    client.pump(0.3)  # a heartbeat already on its way when the fault landed
    start = len(client.received)
    arm = client.send(MessageType.ARM, drone, ArmPayload())
    client.pump(2.0)
    during = client.received[start:]
    restored = client.wait_for(
        lambda m: m.drone_id == drone and m.type is MessageType.ALERT, timeout_s=5
    )
    beat = len(client.received)
    client.pump(1.5)

    assert fault.status == 201
    assert during == []  # no telemetry and no reply to the ARM
    assert restored.payload.code == "LINK_RESTORED"
    assert restored.payload.severity == "INFO"
    assert any(m.message.type is MessageType.HEARTBEAT for m in client.received[beat:])
    assert all(reply.command_msg_id != arm.msg_id for reply in client.replies)
    dropped = [
        line
        for line in sim.logs()
        if line.get("msg") == DROPPED_LOG and line.get("msg_id") == arm.msg_id
    ]
    assert len(dropped) == 1  # handled once: acked, never redelivered
    assert queued(broker) == 0


def test_reply_delay_holds_one_drones_reply_and_not_the_others(
    start_sim: StartSim, client: CommandClient
) -> None:
    sim = start_sim([])
    slow, fast = sim.control.spawn(f"f{secrets.token_hex(4)}", 2)
    client.watch(slow)
    client.watch(fast)
    fault = sim.control.call(
        "POST", f"/drones/{slow}/faults", {"kind": "REPLY_DELAY", "delay_s": 1.5}
    )

    sent_at = time.monotonic()
    slow_arm = client.send(MessageType.ARM, slow, ArmPayload())
    fast_arm = client.send(MessageType.ARM, fast, ArmPayload())
    client.wait_reply(fast_arm.msg_id)
    fast_at = time.monotonic()
    client.wait_reply(slow_arm.msg_id)
    slow_at = time.monotonic()

    assert fault.status == 201
    assert fast_at - sent_at < 1.0
    assert slow_at - sent_at >= 1.5
