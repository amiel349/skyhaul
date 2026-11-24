"""A Testcontainers Postgres for the session, migrated to head with the real Alembic scripts."""

from __future__ import annotations

import secrets
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from skyhaul_fleet_core.migrate import alembic_config
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

# Same tag as deploy/compose.yaml.
POSTGRES_IMAGE = "postgres:16-alpine"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    container = PostgresContainer(POSTGRES_IMAGE, password=secrets.token_hex(16), driver="psycopg")
    with container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def alembic_cfg(database_url: str) -> Config:
    return alembic_config(database_url)


@pytest.fixture(scope="session")
def engine(database_url: str, alembic_cfg: Config) -> Iterator[Engine]:
    command.upgrade(alembic_cfg, "head")
    engine = create_engine(database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A session whose work is rolled back, so tests leave no rows behind."""
    with Session(engine) as session:
        yield session
        session.rollback()
