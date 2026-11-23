"""Topology helpers without a broker. The real declare runs in tests/integration."""

from __future__ import annotations

from typing import Any

import pytest
from skyhaul_contracts import MessageType
from skyhaul_contracts import topology as t


class RecordingChannel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        def record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))

        return record


def test_commands_go_to_fleet_cmd_and_telemetry_to_fleet_telemetry() -> None:
    commands = {"ARM", "TAKEOFF", "GOTO", "RETURN_HOME", "LAND", "SET_SPEED", "START_MISSION"}
    for type_ in MessageType:
        expected = "fleet.cmd" if type_.value in commands else "fleet.telemetry"
        assert t.exchange_for(type_) == expected, type_


def test_drone_binding_matches_every_type_of_one_drone() -> None:
    assert t.drone_binding("gw1-d002") == "drone.gw1-d002.*"
    with pytest.raises(ValueError, match="routing-key word"):
        t.drone_binding("gw1.*")


def test_declare_topology_declares_durable_exchanges_and_bounded_queues() -> None:
    channel = RecordingChannel()

    t.declare_topology(channel)

    assert channel.calls == [
        ("exchange_declare", ("fleet.cmd",), {"exchange_type": "topic", "durable": True}),
        ("exchange_declare", ("fleet.telemetry",), {"exchange_type": "topic", "durable": True}),
        ("exchange_declare", ("fleet.dlx",), {"exchange_type": "fanout", "durable": True}),
        (
            "queue_declare",
            ("fleet.dead",),
            {
                "durable": True,
                "arguments": {
                    "x-max-length": 10_000,
                    "x-max-length-bytes": 655_360_000,
                    "x-overflow": "drop-head",
                },
            },
        ),
        ("queue_bind", ("fleet.dead", "fleet.dlx"), {}),
        (
            "queue_declare",
            ("drone_sim.cmd",),
            {
                "durable": True,
                "arguments": {
                    "x-queue-type": "quorum",
                    "x-single-active-consumer": True,
                    "x-delivery-limit": 5,
                    "x-dead-letter-exchange": "fleet.dlx",
                    "x-max-length": 10_000,
                    "x-max-length-bytes": 655_360_000,
                    "x-overflow": "reject-publish",
                },
            },
        ),
        ("queue_bind", ("drone_sim.cmd", "fleet.cmd"), {"routing_key": "drone.*.*"}),
    ]


def test_declare_debug_tap_is_bounded_and_drops_oldest() -> None:
    channel = RecordingChannel()

    t.declare_debug_tap(channel)

    name, args, kwargs = channel.calls[0]
    assert (name, args) == ("queue_declare", ("debug.telemetry",))
    assert kwargs["arguments"] == {"x-max-length": 1000, "x-overflow": "drop-head"}
    assert channel.calls[1] == (
        "queue_bind",
        ("debug.telemetry", "fleet.telemetry"),
        {"routing_key": "drone.*.*"},
    )


def test_queue_byte_bound_is_the_length_bound_times_the_message_size_limit() -> None:
    from skyhaul_contracts.codec import MAX_MESSAGE_BYTES
    from skyhaul_contracts.topology import MAX_QUEUE_BYTES, MAX_QUEUE_LENGTH

    assert MAX_QUEUE_BYTES == MAX_QUEUE_LENGTH * MAX_MESSAGE_BYTES
