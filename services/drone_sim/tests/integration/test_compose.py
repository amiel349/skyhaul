"""The compose file keeps the simulator out of the default stack and its control port off
the network (ADR 0004 as amended)."""

import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="needs the docker CLI"),
]

COMPOSE_FILE = Path(__file__).resolve().parents[4] / "deploy" / "compose.yaml"
SECRETS = (
    "POSTGRES_USER",
    "POSTGRES_DB",
    "POSTGRES_PASSWORD",
    "RABBITMQ_USER",
    "RABBITMQ_PASSWORD",
    "REDIS_PASSWORD",
    "SIM_CONTROL_TOKEN",
)


def compose_config(*profiles: str) -> dict[str, Any]:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    env |= {name: secrets.token_hex(32) for name in SECRETS}  # throwaway values, no .env
    flags = [arg for profile in profiles for arg in ("--profile", profile)]
    done = subprocess.run(  # noqa: S603 - fixed docker arguments
        ["docker", "compose", "-f", str(COMPOSE_FILE), *flags, "config", "--format", "json"],  # noqa: S607
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    config: dict[str, Any] = json.loads(done.stdout)
    return config


def test_simulator_runs_only_in_the_test_profile_and_publishes_only_on_localhost() -> None:
    default = compose_config()
    test = compose_config("test")

    assert "drone-sim" not in default["services"]
    ports = test["services"]["drone-sim"]["ports"]
    assert [(p["host_ip"], p["published"], p["target"]) for p in ports] == [
        ("127.0.0.1", "8100", 8100)
    ]
    for service in test["services"].values():
        for port in service.get("ports", []):
            assert port.get("host_ip") == "127.0.0.1"  # never 0.0.0.0 or all interfaces
