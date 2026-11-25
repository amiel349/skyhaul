"""A real Postgres (migrated to head) and Redis for the session, and an API app on them.

Tests isolate by unique emails and drone ids; Redis (rate-limit counters) is flushed before
each test.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest
import redis
from alembic import command
from flask import Flask
from flask.testing import FlaskClient
from skyhaul_api import create_app
from skyhaul_api.auth.passwords import hash_password
from skyhaul_api.config import Config
from skyhaul_fleet_core.migrate import alembic_config
from skyhaul_fleet_core.models import Role, User
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

POSTGRES_IMAGE = "postgres:16-alpine"
REDIS_IMAGE = "redis:7.4-alpine"
# Generated per run: test credentials are never literals.
PASSWORD = secrets.token_urlsafe(18)


@dataclass(frozen=True)
class Stack:
    database_url: str
    redis_url: str


@pytest.fixture(scope="session")
def stack() -> Iterator[Stack]:
    redis_password = secrets.token_hex(16)
    postgres = PostgresContainer(POSTGRES_IMAGE, password=secrets.token_hex(16), driver="psycopg")
    redis_container = RedisContainer(REDIS_IMAGE, password=redis_password)
    with postgres, redis_container:
        url = postgres.get_connection_url()
        command.upgrade(alembic_config(url), "head")
        host = redis_container.get_container_host_ip()
        port = redis_container.get_exposed_port(6379)
        yield Stack(url, f"redis://:{redis_password}@{host}:{port}/0")


@pytest.fixture(scope="session")
def api_app(stack: Stack) -> Flask:
    return create_app(
        Config(
            database_url=stack.database_url,
            redis_url=stack.redis_url,
            jwt_secret=secrets.token_hex(32),
        )
    )


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
