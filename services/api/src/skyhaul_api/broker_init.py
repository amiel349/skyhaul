"""``broker-init``: the one-shot that owns the RabbitMQ topology and the per-service users
(ADR 0013, m2-design section 10).

With the admin credentials it creates or updates ``skyhaul_api`` and ``skyhaul_ingestor``
(no tags, so no management or admin access) through the management HTTP API, sets their
permissions, then declares the whole topology through ``skyhaul_contracts.topology``. The
API and the ingestor declare nothing. Idempotent: running it again changes nothing, and a
new password in the environment replaces the old one.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

import pika
from skyhaul_contracts.topology import declare_ingestor_queue, declare_topology
from skyhaul_fleet_core.settings import optional_int, require, require_secret

log = logging.getLogger(__name__)

API_USER: Final = "skyhaul_api"
INGESTOR_USER: Final = "skyhaul_ingestor"


@dataclass(frozen=True)
class Grant:
    configure: str
    write: str
    read: str


PERMISSIONS: Final[dict[str, Grant]] = {
    # Publishes commands to fleet.cmd; reads and configures nothing.
    API_USER: Grant(configure="^$", write=r"^fleet\.cmd$", read="^$"),
    # Consumes its own queue; publishes and configures nothing.
    INGESTOR_USER: Grant(configure="^$", write="^$", read=r"^ingestor\.telemetry$"),
}

READY_WAIT_S: Final = 60.0
HTTP_TIMEOUT_S: Final = 5.0


@dataclass(frozen=True)
class BrokerInitConfig:
    host: str
    admin_user: str
    admin_password: str = field(repr=False)
    passwords: dict[str, str] = field(repr=False)
    amqp_port: int = 5672
    management_port: int = 15672
    vhost: str = "/"


def load_config() -> BrokerInitConfig:
    return BrokerInitConfig(
        host=require("RABBITMQ_HOST"),
        admin_user=require("RABBITMQ_USER"),
        admin_password=require("RABBITMQ_PASSWORD"),
        passwords={
            API_USER: require_secret("AMQP_API_PASSWORD", min_length=16),
            INGESTOR_USER: require_secret("AMQP_INGESTOR_PASSWORD", min_length=16),
        },
        amqp_port=optional_int("RABBITMQ_PORT", 5672),
        management_port=optional_int("RABBITMQ_MANAGEMENT_PORT", 15672),
        vhost=os.environ.get("RABBITMQ_VHOST", "") or "/",
    )


class BrokerInitError(RuntimeError):
    """The management API refused a call. The message names the call, never a password."""


def run(config: BrokerInitConfig, sleep: Callable[[float], None] = time.sleep) -> None:
    management = Management(config)
    management.wait_ready(sleep)
    vhost = urllib.parse.quote(config.vhost, safe="")
    for user, grant in PERMISSIONS.items():
        management.put(f"/api/users/{user}", {"password": config.passwords[user], "tags": ""}, user)
        management.put(
            f"/api/permissions/{vhost}/{user}",
            {"configure": grant.configure, "write": grant.write, "read": grant.read},
            user,
        )
        log.info("broker user ready", extra={"broker_user": user})
    declare(config)
    log.info("broker topology declared")


def declare(config: BrokerInitConfig) -> None:
    params = pika.ConnectionParameters(
        host=config.host,
        port=config.amqp_port,
        virtual_host=config.vhost,
        credentials=pika.PlainCredentials(config.admin_user, config.admin_password),
    )
    connection = pika.BlockingConnection(params)
    try:
        channel = connection.channel()
        declare_topology(channel)
        declare_ingestor_queue(channel)
    finally:
        connection.close()


class Management:
    """The few management HTTP API calls broker-init needs, with the admin credentials."""

    def __init__(self, config: BrokerInitConfig) -> None:
        self._base = f"http://{config.host}:{config.management_port}"
        raw = f"{config.admin_user}:{config.admin_password}".encode()
        self._auth = "Basic " + base64.b64encode(raw).decode()

    def wait_ready(self, sleep: Callable[[float], None]) -> None:
        """The management plugin starts a little after the AMQP port answers."""
        deadline = time.monotonic() + READY_WAIT_S
        while True:
            try:
                self._call("GET", "/api/overview")
                return
            except urllib.error.HTTPError as exc:
                raise BrokerInitError(f"the management API answered HTTP {exc.code}") from None
            except (urllib.error.URLError, OSError) as exc:
                if time.monotonic() >= deadline:
                    raise BrokerInitError("the management API did not answer") from exc
                sleep(1.0)

    def put(self, path: str, body: dict[str, str], user: str) -> None:
        try:
            self._call("PUT", path, json.dumps(body).encode())
        except urllib.error.HTTPError as exc:
            raise BrokerInitError(f"PUT for {user} failed with HTTP {exc.code}") from None

    def _call(self, method: str, path: str, data: bytes | None = None) -> None:
        request = urllib.request.Request(  # noqa: S310 - fixed http scheme, our own broker
            self._base + path,
            data=data,
            method=method,
            headers={"Authorization": self._auth, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S):  # noqa: S310
            pass
