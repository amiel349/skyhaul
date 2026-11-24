"""A Testcontainers RabbitMQ for the whole session, and the simulator as a real subprocess
(`python -m skyhaul_drone_sim`), so signals and the real main thread are exercised."""

from __future__ import annotations

import base64
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pika
import pytest
from sim_support.command_client import CommandClient
from sim_support.control_client import ControlClient
from testcontainers.community.rabbitmq import RabbitMqContainer

# Same tag as deploy/compose.yaml, so tests run against the broker the stack uses.
RABBITMQ_IMAGE = "rabbitmq:3.13-management-alpine"
MANAGEMENT_PORT = 15672
TESTS_DIR = Path(__file__).resolve().parents[1]
READY_TIMEOUT_S = 30.0


@dataclass
class Broker:
    container: RabbitMqContainer
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    management_url: str = ""
    auth: str = field(default="", repr=False)

    @property
    def params(self) -> pika.ConnectionParameters:
        return pika.ConnectionParameters(
            host=self.host,
            port=self.port,
            credentials=pika.PlainCredentials(self.user, self.password),
        )

    def api(self, method: str, path: str) -> Any:
        """Call the management API. Retries while the plugin is still starting."""
        request = urllib.request.Request(  # noqa: S310 - local container URL
            f"{self.management_url}/api/{path}",
            method=method,
            headers={"Authorization": self.auth, "X-Reason": "integration test"},
        )
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
                    raw = response.read()
                    return json.loads(raw) if raw else None
            except (urllib.error.URLError, ConnectionError):
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)

    def queue(self, name: str) -> dict[str, Any]:
        result: dict[str, Any] = self.api("GET", f"queues/%2F/{urllib.parse.quote(name)}")
        return result

    def rabbitmqctl(self, *args: str) -> None:
        code, output = self.container.exec(["rabbitmqctl", *args])
        assert code == 0, output

    def dead_letters(self) -> list[bytes]:
        """Drain `fleet.dead` and return the bodies (each test looks for its own)."""
        connection = pika.BlockingConnection(self.params)
        channel = connection.channel()
        bodies: list[bytes] = []
        while True:
            method, _, body = channel.basic_get("fleet.dead", auto_ack=True)
            if method is None:
                break
            bodies.append(body)
        connection.close()
        return bodies


@pytest.fixture(scope="session")
def broker() -> Iterator[Broker]:
    # Fresh credentials per run: no well-known test password.
    user, password = "skyhaul-test", secrets.token_urlsafe(24)
    container = RabbitMqContainer(RABBITMQ_IMAGE, username=user, password=password)
    container.with_exposed_ports(MANAGEMENT_PORT)
    with container:
        host = container.get_container_host_ip()
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        yield Broker(
            container=container,
            host=host,
            port=int(container.get_exposed_port(5672)),
            user=user,
            password=password,
            management_url=f"http://{host}:{container.get_exposed_port(MANAGEMENT_PORT)}",
            auth=f"Basic {token}",
        )


@dataclass
class SimProcess:
    process: subprocess.Popen[bytes]
    log_path: Path
    control: ControlClient

    def logs(self) -> list[dict[str, Any]]:
        lines = self.log_path.read_text(errors="replace").splitlines()
        return [json.loads(line) for line in lines if line.startswith("{")]

    def has_logged(self, msg: str) -> bool:
        return any(line.get("msg") == msg for line in self.logs())

    def wait_logged(self, msg: str, timeout_s: float = READY_TIMEOUT_S) -> None:
        deadline = time.monotonic() + timeout_s
        while not self.has_logged(msg):
            assert self.process.poll() is None, self.log_path.read_text()
            assert time.monotonic() < deadline, f"simulator never logged {msg!r}"
            time.sleep(0.05)

    def signal(self, signum: int) -> None:
        self.process.send_signal(signum)

    def wait(self, timeout_s: float) -> int:
        return self.process.wait(timeout_s)


type StartSim = Callable[..., SimProcess]


@pytest.fixture
def start_sim(broker: Broker, tmp_path: Path) -> Iterator[StartSim]:
    """Start simulator subprocesses; every one still running is stopped at teardown."""
    started: list[SimProcess] = []

    def start(
        drones: list[str], *, module: str = "skyhaul_drone_sim", wait_ready: bool = True, **env: str
    ) -> SimProcess:
        log_path = tmp_path / f"sim-{len(started)}.log"
        token, port = secrets.token_hex(32), free_port()
        environ = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(TESTS_DIR),
            "RABBITMQ_HOST": broker.host,
            "RABBITMQ_PORT": str(broker.port),
            "RABBITMQ_USER": broker.user,
            "RABBITMQ_PASSWORD": broker.password,
            "SIM_DRONES": ",".join(drones),
            "SIM_CONTROL_TOKEN": token,
            "SIM_CONTROL_PORT": str(port),
            **env,
        }
        log_file = log_path.open("wb")
        process = subprocess.Popen(  # noqa: S603 - our own interpreter and module
            [sys.executable, "-m", module], env=environ, stderr=log_file, stdout=log_file
        )
        sim = SimProcess(process, log_path, ControlClient(f"http://127.0.0.1:{port}", token))
        started.append(sim)
        if wait_ready:
            sim.wait_logged("consuming commands")
        return sim

    yield start
    for sim in started:
        if sim.process.poll() is None:
            sim.signal(signal.SIGTERM)
            try:
                sim.wait(15)
            except subprocess.TimeoutExpired:
                sim.process.kill()
                sim.wait(5)


def free_port() -> int:
    """A port the OS just handed out, for one simulator's control API."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture
def client(broker: Broker) -> Iterator[CommandClient]:
    with CommandClient(broker.params) as command_client:
        yield command_client


@pytest.fixture
def drone(request: pytest.FixtureRequest) -> str:
    """A drone id no other test uses, so leftover commands cannot answer for this one."""
    return f"it{secrets.token_hex(4)}-d001"
