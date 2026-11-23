"""Idempotent topology declare against a real RabbitMQ (issue #6). Needs Docker."""

from __future__ import annotations

import base64
import json
import secrets
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pika
import pytest
from pika.exceptions import ChannelClosedByBroker
from skyhaul_contracts import topology
from testcontainers.community.rabbitmq import RabbitMqContainer

pytestmark = pytest.mark.integration

# Same tag as deploy/compose.yaml, so tests run against the broker the stack uses.
RABBITMQ_IMAGE = "rabbitmq:3.13-management-alpine"


MANAGEMENT_PORT = 15672


@dataclass(frozen=True)
class Broker:
    params: pika.ConnectionParameters
    management_url: str
    auth: str  # Basic auth header value, built from per-run credentials


@pytest.fixture(scope="module")
def broker() -> Iterator[Broker]:
    # Fresh credentials per run (no well-known test password). The guest account cannot log
    # in through a mapped port anyway.
    user, password = "skyhaul-test", secrets.token_urlsafe(24)
    container = RabbitMqContainer(RABBITMQ_IMAGE, username=user, password=password)
    container.with_exposed_ports(MANAGEMENT_PORT)
    with container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(MANAGEMENT_PORT)
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        yield Broker(container.get_connection_params(), f"http://{host}:{port}", f"Basic {token}")


def _queue_arguments(broker: Broker, name: str) -> dict[str, Any]:
    """The arguments the broker recorded for a queue, read from the management API."""
    url = f"{broker.management_url}/api/queues/%2F/{name}"
    request = urllib.request.Request(url, headers={"Authorization": broker.auth})  # noqa: S310
    deadline = time.monotonic() + 30
    while True:  # the management plugin starts a moment after AMQP
        try:
            with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
                arguments: dict[str, Any] = json.load(response)["arguments"]
                return arguments
        except (urllib.error.URLError, ConnectionError):
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


def _channel(params: pika.ConnectionParameters) -> tuple[Any, Any]:
    connection = pika.BlockingConnection(params)
    return connection, connection.channel()


@pytest.mark.timeout(180)
def test_declaring_topology_twice_succeeds_with_the_same_arguments(
    broker: Broker,
) -> None:
    for _ in range(2):  # two separate connections, as two services would
        connection, channel = _channel(broker.params)
        topology.declare_topology(channel)
        topology.declare_debug_tap(channel)
        connection.close()

    connection, channel = _channel(broker.params)
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
    broker: Broker,
) -> None:
    connection, channel = _channel(broker.params)
    topology.declare_topology(channel)
    try:
        with pytest.raises(ChannelClosedByBroker) as exc:
            channel.queue_declare(topology.SIM_COMMAND_QUEUE, durable=True)
        assert exc.value.reply_code == 406  # PRECONDITION_FAILED: arguments differ
    finally:
        connection.close()


@pytest.mark.timeout(180)
def test_command_and_dead_letter_queues_are_length_bounded_on_the_broker(broker: Broker) -> None:
    connection, channel = _channel(broker.params)
    try:
        topology.declare_topology(channel)
    finally:
        connection.close()

    command = _queue_arguments(broker, topology.SIM_COMMAND_QUEUE)
    dead = _queue_arguments(broker, topology.DEAD_LETTER_QUEUE)

    assert command["x-max-length"] == 10_000
    assert command["x-overflow"] == "reject-publish"
    assert command["x-queue-type"] == "quorum"
    assert dead == {"x-max-length": 10_000, "x-overflow": "drop-head"}
