"""A Testcontainers Postgres that runs the real ``deploy/postgres/initdb.d`` scripts, so tests
use the same roles as the stack: migrate as ``skyhaul_owner``, then connect as
``skyhaul_api`` or ``skyhaul_ingestor``. Passwords are generated per run."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from pathlib import Path

from testcontainers.community.postgres import PostgresContainer

POSTGRES_IMAGE = "postgres:16-alpine"
INITDB_DIR = Path(__file__).resolve().parents[3] / "deploy" / "postgres" / "initdb.d"
SERVICE_ROLES = ("skyhaul_owner", "skyhaul_api", "skyhaul_ingestor")


@dataclass
class RolesPostgres:
    container: PostgresContainer
    passwords: dict[str, str] = field(repr=False)

    def url(self, role: str) -> str:
        host = self.container.get_container_host_ip()
        port = self.container.get_exposed_port(5432)
        db = self.container.dbname
        return f"postgresql+psycopg://{role}:{self.passwords[role]}@{host}:{port}/{db}"

    @property
    def superuser_url(self) -> str:
        return self.container.get_connection_url()


def roles_postgres() -> RolesPostgres:
    """Not started yet: use it as ``with pg.container: ...``."""
    passwords = {role: secrets.token_hex(16) for role in SERVICE_ROLES}
    container = PostgresContainer(POSTGRES_IMAGE, password=secrets.token_hex(16), driver="psycopg")
    container.with_volume_mapping(str(INITDB_DIR), "/docker-entrypoint-initdb.d", "ro")
    container.with_env("DB_OWNER_PASSWORD", passwords["skyhaul_owner"])
    container.with_env("DB_API_PASSWORD", passwords["skyhaul_api"])
    container.with_env("DB_INGESTOR_PASSWORD", passwords["skyhaul_ingestor"])
    return RolesPostgres(container, passwords)
