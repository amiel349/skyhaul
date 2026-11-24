"""The simulator process against a real RabbitMQ (issue #7). Needs Docker."""

from __future__ import annotations

import signal
import time
import urllib.parse
import uuid
from typing import Any

import pytest
from sim_support.command_client import CommandClient
from sim_support.messages import minimal_command_payload, raw_command
from skyhaul_contracts import CommandType, MessageType
from skyhaul_contracts.topology import SIM_COMMAND_QUEUE

Broker = Any
StartSim = Any

pytestmark = [pytest.mark.integration, pytest.mark.timeout(180)]

ARM = MessageType.ARM


def send(client: CommandClient, drone: str, type: MessageType = ARM) -> str:
    return client.send(type, drone, minimal_command_payload(CommandType(type.value))).msg_id


def reason(reply: object) -> str:
    return str(reply.message.payload.reason)  # type: ignore[attr-defined]


def poll(condition, timeout_s: float = 30.0, every_s: float = 0.2) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(every_s)


# --- NACK paths ---


def test_unknown_type_gets_unknown_type_nack_with_the_request_ids_and_a_warning(
    start_sim: StartSim, client: CommandClient, drone: str
) -> None:
    sim = start_sim([drone])
    client.watch(drone)
    key, body, envelope = raw_command("FLIP", drone)

    client.send_raw(key, body)
    reply = client.wait_reply(envelope["msg_id"])

    assert reply.message.type is MessageType.NACK
    assert reason(reply) == "UNKNOWN_TYPE"
    assert reply.message.correlation_id == envelope["correlation_id"]
    assert reply.message.payload.command_type == "FLIP"
    assert reply.properties.delivery_mode == 2  # persistent
    poll(lambda: sim.has_logged("command refused"))
    assert any(
        line["level"] == "WARNING" and line.get("correlation_id") == envelope["correlation_id"]
        for line in sim.logs()
    )
    assert sim.process.poll() is None  # still running


def test_unsupported_version_gets_unsupported_version_nack(
    start_sim: StartSim, client: CommandClient, drone: str
) -> None:
    start_sim([drone])
    client.watch(drone)
    key, body, envelope = raw_command("ARM", drone, schema_version=99)

    client.send_raw(key, body)
    reply = client.wait_reply(envelope["msg_id"])

    assert reason(reply) == "UNSUPPORTED_VERSION"
    assert "99" not in reply.message.payload.detail


def test_invalid_payload_gets_invalid_payload_nack(
    start_sim: StartSim, client: CommandClient, drone: str
) -> None:
    start_sim([drone])
    client.watch(drone)
    key, body, envelope = raw_command("TAKEOFF", drone, payload={"altitude_m": 9000.0})

    client.send_raw(key, body)

    assert reason(client.wait_reply(envelope["msg_id"])) == "INVALID_PAYLOAD"


def test_undecodable_and_mismatched_messages_land_in_fleet_dead_and_consumer_keeps_running(
    broker: Broker, start_sim: StartSim, client: CommandClient, drone: str
) -> None:
    sim = start_sim([drone])
    client.watch(drone)
    other = f"{drone[:-4]}-d002"
    garbage = f"garbage {uuid.uuid4()}".encode()
    _, mismatched, _ = raw_command("ARM", drone)
    _, spoofed_version, _ = raw_command("ARM", other, schema_version=99)

    client.send_raw(f"drone.{drone}.ARM", garbage)
    client.send_raw(f"drone.{other}.ARM", mismatched)  # envelope says `drone`
    client.send_raw(f"drone.{drone}.ARM", spoofed_version)  # hint says `other`
    arm = send(client, drone)

    assert client.wait_reply(arm).message.type is MessageType.ACK
    poll(lambda: sum(line["msg"].startswith("command dead-lettered") for line in sim.logs()) == 3)
    dead: list[bytes] = []
    poll(
        lambda: (
            dead.extend(broker.dead_letters())
            or {garbage, mismatched, spoofed_version} <= set(dead)
        )
    )
    assert all(r.command_msg_id == arm for r in client.replies)  # nothing NACKed for them
    assert sim.process.poll() is None


def test_garbage_unknown_type_and_raising_handler_then_a_valid_arm_still_gets_its_reply(
    start_sim: StartSim, client: CommandClient, drone: str
) -> None:
    sim = start_sim([drone], module="sim_support.faulty_sim")
    client.watch(drone)
    key, unknown, unknown_envelope = raw_command("FLIP", drone)

    client.send_raw(f"drone.{drone}.ARM", b"\x00\xff not a message")
    client.send_raw(key, unknown)
    land = send(client, drone, MessageType.LAND)  # its handler raises
    arm = send(client, drone)

    replies = client.wait_replies(3)
    assert [(r.command_msg_id, r.message.type) for r in replies] == [
        (unknown_envelope["msg_id"], MessageType.NACK),
        (land, MessageType.NACK),
        (arm, MessageType.ACK),
    ]
    assert reason(replies[1]) == "INTERNAL_ERROR"
    assert "deliberate" not in replies[1].message.payload.detail
    (raised,) = [line for line in sim.logs() if line["msg"] == "command handler raised"]
    assert "Traceback" in raised["exc"]
    client.pump(1.0)  # the LAND is not redelivered: no second NACK
    assert [r.command_msg_id for r in client.replies].count(land) == 1


def test_command_for_an_unknown_drone_gets_no_reply_and_is_logged(
    start_sim: StartSim, client: CommandClient, drone: str
) -> None:
    sim = start_sim([drone])
    ghost = f"{drone[:-4]}-d404"
    client.watch(ghost)
    client.watch(drone)

    send(client, ghost)
    arm = send(client, drone)

    client.wait_reply(arm)
    assert [r.command_msg_id for r in client.replies] == [arm]
    assert sim.has_logged("command for an unknown drone, not answered")


# --- delivery guarantees ---


def test_command_sent_while_the_simulator_is_stopped_is_handled_when_it_starts(
    broker: Broker, start_sim: StartSim, client: CommandClient, drone: str
) -> None:
    client.watch(drone)
    arm = send(client, drone)  # the queue exists already: the client declared the topology
    queue = broker.queue(SIM_COMMAND_QUEUE)
    assert queue["durable"] is True
    assert queue["arguments"]["x-queue-type"] == "quorum"

    start_sim([drone])

    reply = client.wait_reply(arm)
    assert reply.message.type is MessageType.ACK
    assert reply.properties.delivery_mode == 2  # ACK and NACK are persistent


def test_50_commands_get_replies_in_order_each_with_its_correlation_id(
    start_sim: StartSim, client: CommandClient, drone: str
) -> None:
    start_sim([drone])
    client.watch(drone)
    sent = []
    for n in range(50):
        type = ARM if n % 2 == 0 else MessageType.TAKEOFF
        sent.append(client.send(type, drone, minimal_command_payload(type.value)))

    replies = client.wait_replies(50)

    assert [r.command_msg_id for r in replies] == [m.msg_id for m in sent]
    assert [r.message.correlation_id for r in replies] == [m.correlation_id for m in sent]


def test_second_simulator_does_not_consume_while_the_first_is_active(
    broker: Broker, start_sim: StartSim, client: CommandClient, drone: str
) -> None:
    first = start_sim([drone])
    second = start_sim([drone])
    client.watch(drone)

    sent = [send(client, drone) for _ in range(50)]
    replies = client.wait_replies(50)

    assert [r.command_msg_id for r in replies] == sent
    handled = [
        sum(line["msg"] == "command handled" for line in sim.logs()) for sim in (first, second)
    ]
    assert sorted(handled) == [0, 50]


# --- backpressure and reconnect (the broker blocks publishers on a memory alarm) ---


@pytest.fixture
def memory_alarm(broker: Broker):  # type: ignore[no-untyped-def]
    """`on()` makes the broker block every publishing connection; cleared at teardown."""

    class Alarm:
        def on(self) -> None:
            broker.rabbitmqctl("set_vm_memory_high_watermark", "0")
            poll(lambda: all(node.get("mem_alarm") for node in broker.api("GET", "nodes")))

        def off(self) -> None:
            broker.rabbitmqctl("set_vm_memory_high_watermark", "0.4")

    alarm = Alarm()
    yield alarm
    alarm.off()


def publisher_connection(broker: Broker) -> dict[str, object]:
    connections = broker.api("GET", "connections")
    (connection,) = [
        c
        for c in connections
        if c.get("client_properties", {}).get("connection_name") == "skyhaul-drone-sim publisher"
    ]
    return connection  # type: ignore[no-any-return]


def queued(broker: Broker) -> int:
    queue = broker.queue(SIM_COMMAND_QUEUE)
    return int(queue.get("messages_ready", 0)) + int(queue.get("messages_unacknowledged", 0))


def test_full_outbox_blocks_the_consumer_and_nothing_is_lost_once_it_drains(
    broker: Broker, start_sim: StartSim, client: CommandClient, drone: str, memory_alarm
) -> None:  # type: ignore[no-untyped-def]
    total = 1500
    client.watch(drone)
    sent = [send(client, drone) for _ in range(total)]  # queued before the simulator starts
    memory_alarm.on()

    sim = start_sim([drone])  # outbox_max is 1000 by default

    # The publisher is blocked, the outbox fills up, and the consumer stops taking commands:
    # the rest stay in RabbitMQ (1000 in the outbox, one in the publisher, one blocked put).
    poll(lambda: 0 < queued(broker) <= total - 1000)
    left = queued(broker)
    client.pump(2.0)
    assert queued(broker) == left  # the consumer is blocked, not slowly draining
    assert left >= total - 1003  # at most one publish got through before the block
    assert sim.process.poll() is None

    memory_alarm.off()
    replies = client.wait_replies(total, timeout_s=90)

    assert [r.command_msg_id for r in replies] == sent


def test_publisher_reconnects_after_a_forced_close_and_still_sends_the_waiting_messages(
    broker: Broker, start_sim: StartSim, client: CommandClient, drone: str, memory_alarm
) -> None:  # type: ignore[no-untyped-def]
    client.watch(drone)
    sent = [send(client, drone) for _ in range(50)]
    memory_alarm.on()
    sim = start_sim([drone])
    poll(lambda: queued(broker) == 0)  # all 50 handled: their replies wait in the outbox
    poll(lambda: publisher_connection(broker).get("state") in {"blocked", "blocking"})

    name = publisher_connection(broker)["name"]
    broker.api("DELETE", f"connections/{urllib.parse.quote(str(name), safe='')}")
    memory_alarm.off()

    replies = client.wait_replies(50, timeout_s=60)
    assert [r.command_msg_id for r in replies] == sent
    poll(lambda: sim.has_logged("publisher reconnected"))


# --- shutdown ---


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"])
def test_signal_with_messages_in_flight_exits_0_within_10s_with_the_outbox_drained(
    broker: Broker, start_sim: StartSim, client: CommandClient, drone: str, signum: int
) -> None:
    client.watch(drone)
    total = 300
    sent = [send(client, drone) for _ in range(total)]
    sim = start_sim([drone])
    poll(lambda: queued(broker) < total, every_s=0.01)  # handling has started

    started = time.monotonic()
    sim.signal(signum)
    code = sim.wait(15)
    elapsed = time.monotonic() - started

    assert code == 0
    assert elapsed < 10.0
    (stopped,) = [line for line in sim.logs() if line["msg"] == "simulator stopped"]
    assert stopped["dropped"] == 0
    # Everything acked was answered; everything else was requeued and is answered by the
    # next instance. Nothing is lost.
    answered = {r.command_msg_id for r in client.wait_replies(stopped["published"])}
    start_sim([drone])
    client.wait_replies(total, timeout_s=60)
    assert {r.command_msg_id for r in client.replies} == set(sent)
    assert len(answered) == stopped["published"]


def test_unreachable_broker_at_startup_exits_non_zero_with_a_clear_log(
    start_sim: StartSim,
) -> None:
    sim = start_sim(
        [],
        wait_ready=False,
        RABBITMQ_HOST="127.0.0.1",
        RABBITMQ_PORT="1",
        SIM_CONNECT_TIMEOUT_S="1",
    )

    assert sim.wait(20) == 1
    assert sim.has_logged("publisher could not connect")


def test_missing_password_exits_at_startup_naming_the_variable(start_sim: StartSim) -> None:
    sim = start_sim([], wait_ready=False, RABBITMQ_PASSWORD="")

    assert sim.wait(20) == 2
    (line,) = [line for line in sim.logs() if line["msg"] == "invalid configuration"]
    assert line["error"] == "RABBITMQ_PASSWORD must be set"
