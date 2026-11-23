"""Idempotent topology declare against a real RabbitMQ (issue #6). Needs Docker."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pika
import pytest
from pika.exceptions import ChannelClosedByBroker
from skyhaul_contracts import topology
from testcontainers.community.rabbitmq import RabbitMqContainer

pytestmark = pytest.mark.integration

# Same tag as deploy/compose.yaml, so tests run against the broker the stack uses.
RABBITMQ_IMAGE = "rabbitmq:3.13-management-alpine"


@pytest.fixture(scope="module")
def broker() -> Iterator[pika.ConnectionParameters]:
    with RabbitMqContainer(RABBITMQ_IMAGE) as container:
        yield container.get_connection_params()


def _channel(params: pika.ConnectionParameters) -> tuple[Any, Any]:
    connection = pika.BlockingConnection(params)
    return connection, connection.channel()


@pytest.mark.timeout(180)
def test_declaring_topology_twice_succeeds_with_the_same_arguments(
    broker: pika.ConnectionParameters,
) -> None:
    for _ in range(2):  # two separate connections, as two services would
        connection, channel = _channel(broker)
        topology.declare_topology(channel)
        topology.declare_debug_tap(channel)
        connection.close()

    connection, channel = _channel(broker)
    try:
        for name in (topology.SIM_COMMAND_QUEUE, topology.DEAD_LETTER_QUEUE):
            channel.queue_declare(name, passive=True)
        for name in (
            topology.COMMAND_EXCHANGE,
            topology.TELEMETRY_EXCHANGE,
            topology.DEAD_LETTER_EXCHANGE,
        ):
            channel.exchange_declare(name, passive=True)
    finally:
        connection.close()


@pytest.mark.timeout(180)
def test_command_queue_really_is_quorum_so_a_classic_redeclare_is_refused(
    broker: pika.ConnectionParameters,
) -> None:
    connection, channel = _channel(broker)
    topology.declare_topology(channel)
    try:
        with pytest.raises(ChannelClosedByBroker) as exc:
            channel.queue_declare(topology.SIM_COMMAND_QUEUE, durable=True)
        assert exc.value.reply_code == 406  # PRECONDITION_FAILED: arguments differ
    finally:
        connection.close()
