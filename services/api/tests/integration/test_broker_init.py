"""``broker-init`` against a real RabbitMQ (#29): it creates the service users and the whole
topology (twice, idempotently); ``skyhaul_api`` can only write to ``fleet.cmd``,
``skyhaul_ingestor`` can only read ``ingestor.telemetry``, neither reaches the management
API, and a publish to a forbidden exchange fails."""

from __future__ import annotations

import base64
import secrets
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any

import pika
import pytest
from pika.exceptions import ChannelClosedByBroker
from skyhaul_api import broker_init
from skyhaul_api.broker_init import API_USER, INGESTOR_USER, BrokerInitConfig
from skyhaul_contracts.topology import (
    COMMAND_EXCHANGE,
    DEAD_LETTER_QUEUE,
    INGESTOR_QUEUE,
    SIM_COMMAND_QUEUE,
    TELEMETRY_EXCHANGE,
)
from testcontainers.community.rabbitmq import RabbitMqContainer

from .conftest import RABBITMQ_IMAGE

pytestmark = pytest.mark.integration

ACCESS_REFUSED = 403
MANAGEMENT_PORT = 15672


@pytest.fixture(scope="module")
def broker() -> Iterator[BrokerInitConfig]:
    admin, admin_password = "skyhaul-admin", secrets.token_urlsafe(24)
    rabbit = RabbitMqContainer(RABBITMQ_IMAGE, username=admin, password=admin_password)
    rabbit.with_exposed_ports(MANAGEMENT_PORT)
    with rabbit:
        config = BrokerInitConfig(
            host=rabbit.get_container_host_ip(),
            admin_user=admin,
            admin_password=admin_password,
            passwords={API_USER: secrets.token_hex(16), INGESTOR_USER: secrets.token_hex(16)},
            amqp_port=int(rabbit.get_exposed_port(5672)),
            management_port=int(rabbit.get_exposed_port(MANAGEMENT_PORT)),
        )
        broker_init.run(config)
        broker_init.run(config)  # idempotent
        yield config


def connect(config: BrokerInitConfig, user: str) -> Any:
    password = config.admin_password if user == config.admin_user else config.passwords[user]
    return pika.BlockingConnection(
        pika.ConnectionParameters(
            host=config.host,
            port=config.amqp_port,
            credentials=pika.PlainCredentials(user, password),
        )
    )


def refused(config: BrokerInitConfig, user: str, action: Callable[[Any], object]) -> bool:
    connection = connect(config, user)
    try:
        action(connection.channel())
    except ChannelClosedByBroker as exc:
        return exc.reply_code == ACCESS_REFUSED
    finally:
        if connection.is_open:
            connection.close()
    return False


def publish_to(exchange: str) -> Callable[[Any], object]:
    def action(channel: Any) -> None:
        channel.confirm_delivery()
        channel.basic_publish(exchange, "drone.x-d001.ARM", b"{}")

    return action


def test_broker_init_declares_the_whole_topology(broker: BrokerInitConfig) -> None:
    connection = connect(broker, broker.admin_user)
    channel = connection.channel()
    for exchange in (COMMAND_EXCHANGE, TELEMETRY_EXCHANGE, "fleet.dlx"):
        channel.exchange_declare(exchange, passive=True)
    for queue in (SIM_COMMAND_QUEUE, INGESTOR_QUEUE, DEAD_LETTER_QUEUE):
        channel.queue_declare(queue, passive=True)
    connection.close()


def test_the_api_user_writes_fleet_cmd_only(broker: BrokerInitConfig) -> None:
    assert not refused(broker, API_USER, publish_to(COMMAND_EXCHANGE))
    assert refused(broker, API_USER, publish_to(TELEMETRY_EXCHANGE))
    assert refused(broker, API_USER, lambda ch: ch.basic_get(SIM_COMMAND_QUEUE))
    assert refused(broker, API_USER, lambda ch: ch.queue_declare("api.own"))
    assert refused(broker, API_USER, lambda ch: ch.exchange_declare("api.own", "topic"))


def test_the_ingestor_user_reads_its_queue_only(broker: BrokerInitConfig) -> None:
    # What the ingestor does at start: a passive declare, then consume.
    assert not refused(
        broker, INGESTOR_USER, lambda ch: ch.queue_declare(INGESTOR_QUEUE, passive=True)
    )
    assert not refused(broker, INGESTOR_USER, lambda ch: ch.basic_get(INGESTOR_QUEUE))
    assert refused(broker, INGESTOR_USER, lambda ch: ch.basic_get(SIM_COMMAND_QUEUE))
    assert refused(broker, INGESTOR_USER, publish_to(TELEMETRY_EXCHANGE))
    assert refused(broker, INGESTOR_USER, publish_to(COMMAND_EXCHANGE))
    assert refused(broker, INGESTOR_USER, lambda ch: ch.queue_declare("ingestor.own"))


@pytest.mark.parametrize("user", [API_USER, INGESTOR_USER])
def test_service_users_have_no_management_access(broker: BrokerInitConfig, user: str) -> None:
    raw = f"{user}:{broker.passwords[user]}".encode()
    request = urllib.request.Request(
        f"http://{broker.host}:{broker.management_port}/api/whoami",
        headers={"Authorization": "Basic " + base64.b64encode(raw).decode()},
    )
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=5)  # noqa: S310
    assert caught.value.code == 401
