"""What happens before a handler changes anything: duplicates, unknown drones and payloads
out of range, through the real handlers (and the consumer where the Codec decides)."""

import logging
import threading

import pytest
from sim_support.drones import spawned_board
from sim_support.fakes import FakeBroker
from sim_support.flight import Flight, nack_reason, reply_type
from sim_support.messages import DRONE, command, raw_command
from skyhaul_contracts import Codec, MessageType
from skyhaul_contracts.v1 import SetSpeedPayload
from skyhaul_drone_sim.board import JobsBoard
from skyhaul_drone_sim.clock import FakeClock
from skyhaul_drone_sim.consumer import Consumer
from skyhaul_drone_sim.handlers import registry
from skyhaul_drone_sim.model import DEDUP_CACHE_SIZE
from skyhaul_drone_sim.outbox import new_outbox


def make_consumer() -> tuple[Consumer, JobsBoard]:
    board = spawned_board(DRONE)
    consumer = Consumer(
        codec=Codec(),
        board=board,
        registry=registry,
        outbox=new_outbox(10),
        clock=FakeClock(),
        stop=threading.Event(),
        connect=FakeBroker().connect,
        connect_timeout_s=1.0,
        prefetch=1,
    )
    return consumer, board


def test_command_with_a_handled_msg_id_gets_the_same_reply_and_is_not_applied_twice() -> None:
    flight = Flight()
    arm = command("ARM")

    first = flight.send("ARM", msg=arm)
    again = flight.send("ARM", msg=arm)  # applied twice, it would be NACK INVALID_STATE

    assert reply_type(first) is MessageType.ACK
    assert again.replies == first.replies  # the same ACK, msg_id included
    assert again.duplicate


def test_duplicate_of_a_nack_gets_the_same_nack() -> None:
    flight = Flight(battery_pct=10.0)
    arm = command("ARM")

    first = flight.send("ARM", msg=arm)
    flight.set(battery_pct=100.0)

    assert nack_reason(flight.send("ARM", msg=arm)) == nack_reason(first) == "BATTERY_TOO_LOW"


def test_dedup_cache_holds_the_last_256_commands_per_drone() -> None:
    flight = Flight()
    oldest = command("SET_SPEED", payload=SetSpeedPayload(speed_mps=1.0))
    flight.send("SET_SPEED", msg=oldest)
    newest = None
    for _ in range(DEDUP_CACHE_SIZE):
        newest = command("SET_SPEED", payload=SetSpeedPayload(speed_mps=2.0))
        flight.send("SET_SPEED", msg=newest)
    assert newest is not None

    assert flight.send("SET_SPEED", msg=newest).duplicate
    replayed = flight.send("SET_SPEED", msg=oldest)  # beyond the window: handled as new

    assert not replayed.duplicate
    assert flight.drone.cruise_mps == 1.0
    assert len(flight.drone.recent_cmds) == DEDUP_CACHE_SIZE


def test_command_for_a_drone_that_is_not_spawned_gets_no_reply_a_warning_and_is_counted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    consumer, board = make_consumer()
    msg = command("ARM", drone_id="main-d404")

    with caplog.at_level(logging.WARNING):
        outcome = consumer.handle(Codec.routing_key(msg.drone_id, "ARM"), Codec().encode(msg))

    assert outcome.replies == []
    assert not outcome.dead_letter
    assert board.unknown_drone_commands() == 1
    (record,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert record.getMessage() == "command for an unknown drone, not answered"


@pytest.mark.parametrize(
    ("type", "payload"),
    [
        ("SET_SPEED", {"speed_mps": 0.4}),
        ("SET_SPEED", {"speed_mps": -1.0}),
        ("SET_SPEED", {"speed_mps": 20.5}),
        ("TAKEOFF", {"altitude_m": 4.9}),
        ("TAKEOFF", {"altitude_m": 120.5}),
        ("START_MISSION", {"mission_id": "m1", "waypoints": []}),
        (
            "START_MISSION",
            {
                "mission_id": "m1",
                "waypoints": [{"lat": 32.0, "lon": 34.8, "altitude_m": 30.0}] * 51,
            },
        ),
    ],
    ids=[
        "speed-below-0.5",
        "speed-negative",
        "speed-above-20",
        "altitude-below-5",
        "altitude-above-120",
        "no-waypoints",
        "51-waypoints",
    ],
)
def test_payload_out_of_range_gets_nack_invalid_payload(type: str, payload: object) -> None:
    consumer, _ = make_consumer()
    key, body, envelope = raw_command(type, DRONE, payload=payload)  # type: ignore[arg-type]

    outcome = consumer.handle(key, body)

    (reply,) = outcome.replies
    assert reply.message.type is MessageType.NACK
    assert reply.message.payload.reason == "INVALID_PAYLOAD"
    assert reply.message.payload.command_msg_id == envelope["msg_id"]
