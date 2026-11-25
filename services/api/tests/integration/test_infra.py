"""Infrastructure hardening (#29): Redis refuses a client without the password, and the
compose file (rendered by ``docker compose config`` with throwaway values, never the
real ``.env``) hardens the ingestor, starts api and ingestor only after the one-shots, and
lets nothing but broker-init declare. ``.env.example`` keeps every new secret empty."""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
from pathlib import Path
from typing import Any

import pytest
import redis

from .conftest import Stack

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[4]
COMPOSE = ROOT / "deploy" / "compose.yaml"
ENV_EXAMPLE = ROOT / ".env.example"
NEW_SECRETS = (
    "JWT_SECRET",
    "DB_OWNER_PASSWORD",
    "DB_API_PASSWORD",
    "DB_INGESTOR_PASSWORD",
    "AMQP_API_PASSWORD",
    "AMQP_INGESTOR_PASSWORD",
)


def test_redis_refuses_a_client_without_the_password(stack: Stack) -> None:
    url = re.sub(r"//:[^@]*@", "//", stack.redis_url)
    client = redis.Redis.from_url(url)
    with pytest.raises(redis.AuthenticationError):
        client.ping()
    assert redis.Redis.from_url(stack.redis_url).ping()


def test_env_example_keeps_the_new_secrets_empty() -> None:
    values = dict(
        line.split("=", 1)
        for line in ENV_EXAMPLE.read_text().splitlines()
        if re.match(r"^[A-Z_]+=", line)
    )
    assert {key: values.get(key) for key in NEW_SECRETS} == dict.fromkeys(NEW_SECRETS, "")


@pytest.fixture(scope="module")
def services() -> dict[str, Any]:
    """The rendered compose services, with throwaway values for every key of .env.example."""
    keys = [m[1] for m in re.finditer(r"^([A-Z_]+)=", ENV_EXAMPLE.read_text(), re.MULTILINE)]
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    env |= {key: secrets.token_hex(32) for key in keys}
    command = ["docker", "compose", "-f", str(COMPOSE), "--profile", "test", "config"]
    rendered = subprocess.run(  # noqa: S603 - fixed docker arguments
        [*command, "--format", "json"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    loaded: dict[str, Any] = json.loads(rendered.stdout)["services"]
    return loaded


def test_the_ingestor_container_is_hardened_and_unpublished(services: dict[str, Any]) -> None:
    ingestor = services["ingestor"]
    assert ingestor["read_only"] is True
    assert ingestor["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in ingestor["security_opt"]
    assert not ingestor.get("ports")


@pytest.mark.parametrize("service", ["api", "ingestor"])
def test_services_start_after_migrate_and_broker_init(
    services: dict[str, Any], service: str
) -> None:
    depends = services[service]["depends_on"]
    for one_shot in ("migrate", "broker-init"):
        assert depends[one_shot]["condition"] == "service_completed_successfully"


def test_services_use_their_own_users_and_nothing_asks_them_to_declare(
    services: dict[str, Any],
) -> None:
    assert services["broker-init"]["command"][-1] == "broker-init"
    for service, user in (("api", "skyhaul_api"), ("ingestor", "skyhaul_ingestor")):
        environment = services[service]["environment"]
        assert environment["RABBITMQ_USER"] == user
        assert f"://{user}:" in environment["DATABASE_URL"]
        assert not any("DECLARE" in key for key in environment)
    assert "://skyhaul_owner:" in services["migrate"]["environment"]["DATABASE_URL"]
