"""A Testcontainers RabbitMQ for the session, the simulator as a real subprocess, and the
environment an MCP server needs to reach both."""

from __future__ import annotations

import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pika
import pytest
from testcontainers.community.rabbitmq import RabbitMqContainer

# Same tag as deploy/compose.yaml.
RABBITMQ_IMAGE = "rabbitmq:3.13-management-alpine"
READY_TIMEOUT_S = 30.0


@dataclass
class Broker:
    host: str
    port: int
    user: str
    password: str = field(repr=False)

    @property
    def params(self) -> pika.ConnectionParameters:
        return pika.ConnectionParameters(
            host=self.host,
            port=self.port,
            credentials=pika.PlainCredentials(self.user, self.password),
        )


@pytest.fixture(scope="session")
def broker() -> Iterator[Broker]:
    user, password = "skyhaul-test", secrets.token_urlsafe(24)
    with RabbitMqContainer(RABBITMQ_IMAGE, username=user, password=password) as container:
        yield Broker(
            container.get_container_host_ip(),
            int(container.get_exposed_port(5672)),
            user,
            password,
        )


@dataclass
class Sim:
    process: subprocess.Popen[bytes]
    env: dict[str, str] = field(repr=False)
    """What the MCP server needs: SIM_CONTROL_URL, SIM_CONTROL_TOKEN and the broker."""


@pytest.fixture
def sim(broker: Broker, tmp_path: Path) -> Iterator[Sim]:
    """The simulator with no seeded drones, ready on a free control port."""
    token, port = secrets.token_hex(32), _free_port()
    broker_env = {
        "RABBITMQ_HOST": broker.host,
        "RABBITMQ_PORT": str(broker.port),
        "RABBITMQ_USER": broker.user,
        "RABBITMQ_PASSWORD": broker.password,
    }
    env = {
        "PATH": os.environ.get("PATH", ""),
        **broker_env,
        "SIM_CONTROL_TOKEN": token,
        "SIM_CONTROL_PORT": str(port),
    }
    log = (tmp_path / "sim.log").open("wb")
    process = subprocess.Popen(
        [sys.executable, "-m", "skyhaul_drone_sim"], env=env, stdout=log, stderr=log
    )
    url = f"http://127.0.0.1:{port}"
    _wait_healthy(process, url, tmp_path / "sim.log")
    yield Sim(process, {"SIM_CONTROL_URL": url, "SIM_CONTROL_TOKEN": token, **broker_env})
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(5)


def _wait_healthy(process: subprocess.Popen[bytes], url: str, log: Path) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_S
    while True:
        assert process.poll() is None, log.read_text()
        try:
            with urllib.request.urlopen(f"{url}/healthz", timeout=1) as response:  # noqa: S310
                if response.status == 200:
                    return
        except OSError:
            pass
        assert time.monotonic() < deadline, "the simulator never became healthy"
        time.sleep(0.1)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port
