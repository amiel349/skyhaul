"""The database roles from deploy/postgres/initdb.d plus the grants migration (#29): none is
a superuser or may create roles or databases, the owner holds the schema, audit_log is
insert-only for the API, and each service role is refused what it does not need."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from alembic import command
from pg_support import SERVICE_ROLES, RolesPostgres, roles_postgres
from skyhaul_fleet_core.migrate import alembic_config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg() -> Iterator[RolesPostgres]:
    pg = roles_postgres()
    with pg.container:
        command.upgrade(alembic_config(pg.url("skyhaul_owner")), "head")
        yield pg


def run(pg: RolesPostgres, role: str, sql: str) -> None:
    engine = create_engine(pg.url(role))
    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
    finally:
        engine.dispose()


def refused(pg: RolesPostgres, role: str, sql: str) -> bool:
    try:
        run(pg, role, sql)
    except ProgrammingError as exc:
        return "permission denied" in str(exc.orig)
    return False


def test_no_role_is_privileged_and_the_owner_owns_the_tables(pg: RolesPostgres) -> None:
    engine = create_engine(pg.superuser_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls "
                "FROM pg_roles WHERE rolname LIKE 'skyhaul\\_%'"
            )
        ).all()
        owners = set(
            conn.scalars(
                text("SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname = 'public'")
            )
        )
    engine.dispose()
    assert {row.rolname for row in rows} == set(SERVICE_ROLES)
    assert all(not any(row[1:]) for row in rows)
    assert owners == {"skyhaul_owner"}


def test_audit_log_is_insert_only_for_the_api(pg: RolesPostgres) -> None:
    run(
        pg,
        "skyhaul_api",
        "INSERT INTO audit_log (action, outcome, ts) VALUES ('test.insert', 'success', now())",
    )
    assert refused(pg, "skyhaul_api", "UPDATE audit_log SET action = 'x'")
    assert refused(pg, "skyhaul_api", "DELETE FROM audit_log")
    assert refused(pg, "skyhaul_api", "TRUNCATE audit_log")


@pytest.mark.parametrize(
    ("role", "sql"),
    [
        ("skyhaul_api", "CREATE TABLE intruder (id int)"),
        ("skyhaul_api", "DELETE FROM commands"),
        ("skyhaul_api", "DELETE FROM telemetry"),
        ("skyhaul_ingestor", "CREATE TABLE intruder (id int)"),
        ("skyhaul_ingestor", "SELECT 1 FROM users"),
        ("skyhaul_ingestor", "DELETE FROM commands"),
        ("skyhaul_ingestor", "INSERT INTO audit_log (action, outcome, ts) VALUES ('a','b',now())"),
        ("skyhaul_ingestor", "UPDATE drones SET name = 'x'"),
    ],
)
def test_service_roles_are_refused_what_they_do_not_need(
    pg: RolesPostgres, role: str, sql: str
) -> None:
    assert refused(pg, role, sql)


def test_the_ingestor_may_run_telemetry_retention(pg: RolesPostgres) -> None:
    run(pg, "skyhaul_ingestor", "DELETE FROM telemetry WHERE ts < now() - interval '7 days'")
