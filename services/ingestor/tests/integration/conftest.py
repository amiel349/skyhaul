"""Real Postgres (migrated, with a least-privilege ``ingestor`` role), Redis and RabbitMQ for
the session; the ingestor runs on a thread of the test process against them.

Isolation: every test uses its own drone ids (``it<hex>-d001``) and looks only at its rows.
The queue is shared, like in the stack; leftovers from one test belong to drones no other
test registers.
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pika
import pytest
import redis
from alembic import command
from pydantic import BaseModel
from skyhaul_contracts.codec import Codec
from skyhaul_contracts.message import Message
from skyhaul_contracts.topology import TELEMETRY_EXCHANGE, declare_ingestor_queue, declare_topology
from skyhaul_fleet_core.migrate import alembic_config
from skyhaul_fleet_core.models import Drone
from skyhaul_ingestor.config import IngestorConfig
from skyhaul_ingestor.consumer import Ingestor, build, run
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.rabbitmq import RabbitMqContainer
from testcontainers.community.redis import RedisContainer

POSTGRES_IMAGE = "postgres:16-alpine"
REDIS_IMAGE = "redis:7.4-alpine"
RABBITMQ_IMAGE = "rabbitmq:3.13-management-alpine"


@dataclass
class Stack:
    postgres: PostgresContainer
    owner_url: str = field(repr=False)
    ingestor_url: str = field(repr=False)
    redis_url: str = field(repr=False)
    amqp: pika.ConnectionParameters = field(repr=False)
    amqp_user: str = ""
    amqp_password: str = field(default="", repr=False)

    def config(self, tmp: Path, **changes: Any) -> IngestorConfig:
        settings: dict[str, Any] = {
            "database_url": self.ingestor_url,
            "redis_url": self.redis_url,
            "rabbitmq_host": self.amqp.host,
            "rabbitmq_port": self.amqp.port,
            "rabbitmq_user": self.amqp_user,
            "rabbitmq_password": self.amqp_password,
            "alive_file": str(tmp / "alive"),
        }
        return IngestorConfig(**(settings | changes))


@pytest.fixture(scope="session")
def stack() -> Iterator[Stack]:
    user, amqp_password = "skyhaul-test", secrets.token_urlsafe(24)
    redis_password, ingestor_password = secrets.token_hex(16), secrets.token_hex(16)
    postgres = PostgresContainer(POSTGRES_IMAGE, password=secrets.token_hex(16), driver="psycopg")
    rabbit = RabbitMqContainer(RABBITMQ_IMAGE, username=user, password=amqp_password)
    redis_container = RedisContainer(REDIS_IMAGE, password=redis_password)
    with postgres, rabbit, redis_container:
        owner_url = postgres.get_connection_url()
        command.upgrade(alembic_config(owner_url), "head")
        _create_ingestor_role(owner_url, ingestor_password)
        host, port = postgres.get_container_host_ip(), postgres.get_exposed_port(5432)
        ingestor_url = (
            f"postgresql+psycopg://ingestor:{ingestor_password}@{host}:{port}/{postgres.dbname}"
        )
        amqp = pika.ConnectionParameters(
            host=rabbit.get_container_host_ip(),
            port=int(rabbit.get_exposed_port(5672)),
            credentials=pika.PlainCredentials(user, amqp_password),
        )
        connection = pika.BlockingConnection(amqp)
        declare_topology(connection.channel())  # what broker-init (#29) will do
        declare_ingestor_queue(connection.channel())
        connection.close()
        redis_host = redis_container.get_container_host_ip()
        redis_url = (
            f"redis://:{redis_password}@{redis_host}:{redis_container.get_exposed_port(6379)}/0"
        )
        yield Stack(postgres, owner_url, ingestor_url, redis_url, amqp, user, amqp_password)


def _create_ingestor_role(owner_url: str, password: str) -> None:
    """The grants of design section 10 (SELECT where an UPDATE ... RETURNING needs it)."""
    engine = create_engine(owner_url)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE ROLE ingestor LOGIN PASSWORD '{password}'"))
        conn.execute(text("GRANT SELECT, UPDATE ON missions TO ingestor"))
        conn.execute(text("GRANT INSERT ON mission_events TO ingestor"))
        conn.execute(text("GRANT SELECT, INSERT, UPDATE ON alerts TO ingestor"))
        conn.execute(text("GRANT SELECT, INSERT, DELETE ON telemetry TO ingestor"))
        conn.execute(text("GRANT SELECT ON drones TO ingestor"))
        conn.execute(text("GRANT SELECT, UPDATE ON commands TO ingestor"))
    engine.dispose()


@pytest.fixture(scope="session")
def engine(stack: Stack) -> Iterator[Engine]:
    engine = create_engine(stack.owner_url)
    yield engine
    engine.dispose()


@pytest.fixture
def redis_client(stack: Stack) -> redis.Redis:
    return redis.Redis.from_url(stack.redis_url, decode_responses=True)


@pytest.fixture
def drone_id() -> str:
    return f"it{secrets.token_hex(4)}-d001"


@pytest.fixture
def register(engine: Engine) -> Callable[[str], None]:
    def add(drone_id: str) -> None:
        with Session(engine) as session, session.begin():
            session.add(Drone(id=drone_id, name="Test drone", home_lat=32.0, home_lon=34.8))

    return add


@dataclass
class RunningIngestor:
    ingestor: Ingestor
    thread: threading.Thread
    stop: threading.Event
    result: list[int]
    errors: list[BaseException]

    @property
    def counters(self) -> Counter[str]:
        return self.ingestor.counters

    def close(self, timeout_s: float = 10.0) -> int | None:
        self.stop.set()
        self.thread.join(timeout_s)
        assert not self.thread.is_alive(), "the ingestor did not stop"
        return self.result[0] if self.result else None


type StartIngestor = Callable[..., RunningIngestor]


@pytest.fixture
def start_ingestor(stack: Stack, tmp_path: Path) -> Iterator[StartIngestor]:
    started: list[RunningIngestor] = []

    def start(ingestor: Ingestor | None = None, **changes: Any) -> RunningIngestor:
        config = stack.config(tmp_path, **changes)
        ingestor = ingestor or build(config)
        stop, result, errors = threading.Event(), [], []

        def target() -> None:
            try:
                result.append(run(config, stop, ingestor))
            except Exception as exc:  # a test's injected crash
                errors.append(exc)

        thread = threading.Thread(target=target, name="ingestor", daemon=True)
        thread.start()
        running = RunningIngestor(ingestor, thread, stop, result, errors)
        started.append(running)
        return running

    yield start
    for running in started:
        running.stop.set()
        running.thread.join(10)


@pytest.fixture
def publish(stack: Stack) -> Iterator[Callable[[Message[BaseModel] | bytes, str], None]]:
    """Publish a telemetry message (or raw bytes with a routing key) to fleet.telemetry."""
    connection = pika.BlockingConnection(stack.amqp)
    channel = connection.channel()
    codec = Codec()

    def send(msg: Message[BaseModel] | bytes, routing_key: str = "") -> None:
        if isinstance(msg, bytes):
            body = msg
        else:
            body, routing_key = codec.encode(msg), Codec.routing_key(msg.drone_id, msg.type)
        properties = pika.BasicProperties(delivery_mode=pika.DeliveryMode.Persistent)
        channel.basic_publish(TELEMETRY_EXCHANGE, routing_key, body, properties=properties)

    yield send
    connection.close()


@pytest.fixture
def start_sim(stack: Stack, tmp_path: Path) -> Iterator[Callable[[list[str]], None]]:
    """The real simulator as a subprocess, seeded with the given drones."""
    processes: list[subprocess.Popen[bytes]] = []

    def start(drones: list[str]) -> None:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "RABBITMQ_HOST": stack.amqp.host,
            "RABBITMQ_PORT": str(stack.amqp.port),
            "RABBITMQ_USER": stack.amqp_user,
            "RABBITMQ_PASSWORD": stack.amqp_password,
            "SIM_DRONES": ",".join(drones),
            "SIM_CONTROL_TOKEN": secrets.token_hex(32),
            "SIM_CONTROL_PORT": str(_free_port()),
        }
        log = (tmp_path / "sim.log").open("wb")
        processes.append(
            subprocess.Popen(
                [sys.executable, "-m", "skyhaul_drone_sim"], env=env, stderr=log, stdout=log
            )
        )

    yield start
    for process in processes:
        process.terminate()
        process.wait(15)


def wait_until[T](probe: Callable[[], T], timeout_s: float = 20.0, what: str = "condition") -> T:
    deadline = time.monotonic() + timeout_s
    while True:
        value = probe()
        if value:
            return value
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.1)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port
