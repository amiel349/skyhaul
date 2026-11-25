"""A real Postgres (the stack's roles from deploy/postgres/initdb.d, migrated to head as
``skyhaul_owner``), Redis and RabbitMQ (topology declared, as broker-init does) for the
session, and an API app on them, connected as ``skyhaul_api``, with its real command
publisher. Tests write their fixtures as the owner (``database_url``).

Tests isolate by unique emails and drone ids; Redis (rate-limit counters) is flushed before
each test. The command tests also start the ingestor (a thread) and the simulator (a
subprocess); see ``test_commands.py``.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import pika
import pytest
import redis
from alembic import command
from flask import Flask
from flask.testing import FlaskClient
from pg_support import roles_postgres
from skyhaul_api import create_app
from skyhaul_api.auth.passwords import hash_password
from skyhaul_api.commands.routes import PUBLISHER_KEY
from skyhaul_api.config import Config
from skyhaul_api.realtime import stop_realtime
from skyhaul_contracts.topology import declare_ingestor_queue, declare_topology
from skyhaul_fleet_core.migrate import alembic_config
from skyhaul_fleet_core.models import Role, User
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from testcontainers.community.rabbitmq import RabbitMqContainer
from testcontainers.community.redis import RedisContainer

REDIS_IMAGE = "redis:7.4-alpine"
RABBITMQ_IMAGE = "rabbitmq:3.13-management-alpine"
AMQP_USER = "skyhaul-test"
# Generated per run: test credentials are never literals.
PASSWORD = secrets.token_urlsafe(18)


@dataclass(frozen=True)
class Stack:
    database_url: str = field(repr=False)  # skyhaul_owner: test fixtures and assertions
    api_database_url: str = field(repr=False)  # skyhaul_api: the app under test
    ingestor_database_url: str = field(repr=False)  # skyhaul_ingestor: the ingestor thread
    redis_url: str = field(repr=False)
    amqp_host: str
    amqp_port: int
    amqp_password: str = field(repr=False)
    rabbit: RabbitMqContainer = field(repr=False)

    def amqp(self, vhost: str = "/") -> pika.ConnectionParameters:
        return pika.ConnectionParameters(
            host=self.amqp_host,
            port=self.amqp_port,
            virtual_host=vhost,
            credentials=pika.PlainCredentials(AMQP_USER, self.amqp_password),
        )

    def config(self, **changes: object) -> Config:
        settings: dict[str, object] = {
            "database_url": self.api_database_url,
            "redis_url": self.redis_url,
            "jwt_secret": secrets.token_hex(32),
            "rabbitmq_host": self.amqp_host,
            "rabbitmq_port": self.amqp_port,
            "rabbitmq_user": AMQP_USER,
            "rabbitmq_password": self.amqp_password,
        }
        return Config(**(settings | changes))  # type: ignore[arg-type]


@pytest.fixture(scope="session")
def stack() -> Iterator[Stack]:
    redis_password, amqp_password = secrets.token_hex(16), secrets.token_urlsafe(24)
    pg = roles_postgres()
    redis_container = RedisContainer(REDIS_IMAGE, password=redis_password)
    rabbit = RabbitMqContainer(RABBITMQ_IMAGE, username=AMQP_USER, password=amqp_password)
    with pg.container, redis_container, rabbit:
        url = pg.url("skyhaul_owner")
        command.upgrade(alembic_config(url), "head")
        host = redis_container.get_container_host_ip()
        port = redis_container.get_exposed_port(6379)
        stack = Stack(
            url,
            pg.url("skyhaul_api"),
            pg.url("skyhaul_ingestor"),
            f"redis://:{redis_password}@{host}:{port}/0",
            rabbit.get_container_host_ip(),
            int(rabbit.get_exposed_port(5672)),
            amqp_password,
            rabbit,
        )
        connection = pika.BlockingConnection(stack.amqp())
        declare_topology(connection.channel())  # what broker-init does in the stack
        declare_ingestor_queue(connection.channel())
        connection.close()
        yield stack


@pytest.fixture(scope="session")
def api_app(stack: Stack) -> Iterator[Flask]:
    app = create_app(stack.config())
    yield app
    app.extensions[PUBLISHER_KEY].stop()
    stop_realtime(app)


@pytest.fixture(autouse=True)
def _flush_rate_limits(request: pytest.FixtureRequest) -> None:
    if "stack" in request.fixturenames:
        redis.Redis.from_url(request.getfixturevalue("stack").redis_url).flushdb()


@pytest.fixture
def api(api_app: Flask) -> FlaskClient:
    return api_app.test_client()


@pytest.fixture
def db_session(stack: Stack) -> Iterator[Session]:
    engine = create_engine(stack.database_url)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


@pytest.fixture
def password() -> str:
    """The password of every user made by ``make_user``."""
    return PASSWORD


@pytest.fixture
def make_user(db_session: Session) -> Callable[[Role], User]:
    def make(role: Role) -> User:
        user = User(
            email=f"{role.value}-{uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password(PASSWORD),
            role=role,
        )
        db_session.add(user)
        db_session.commit()
        return user

    return make


@pytest.fixture
def token_for(api: FlaskClient) -> Callable[[User], str]:
    def login(user: User) -> str:
        response = api.post("/api/v1/auth/login", json={"email": user.email, "password": PASSWORD})
        assert response.status_code == 200, response.get_json()
        token: str = response.get_json()["access_token"]
        return token

    return login


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
