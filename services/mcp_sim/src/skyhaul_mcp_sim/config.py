"""Settings from environment variables only. Secrets have no defaults.

A missing secret does not stop the server: the tools that need it answer "not configured".
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

import pika

from skyhaul_mcp_sim.errors import ToolFailure, not_configured

DEFAULT_CONTROL_URL: Final = "http://127.0.0.1:8100"
DEFAULT_RABBITMQ_HOST: Final = "127.0.0.1"
DEFAULT_RABBITMQ_PORT: Final = "5672"
BROKER_TIMEOUT_S: Final = 5.0


def _read(env: Mapping[str, str], name: str) -> str | None:
    """Empty, or an unexpanded `${VAR}` left by the MCP client, counts as missing."""
    value = env.get(name, "").strip()
    if not value or value.startswith("${"):
        return None
    return value


@dataclass(frozen=True)
class Config:
    control_url: str
    control_token: str | None = field(repr=False)
    rabbitmq_host: str
    rabbitmq_port: str
    rabbitmq_user: str | None
    rabbitmq_password: str | None = field(repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        return cls(
            control_url=_read(env, "SIM_CONTROL_URL") or DEFAULT_CONTROL_URL,
            control_token=_read(env, "SIM_CONTROL_TOKEN"),
            rabbitmq_host=_read(env, "RABBITMQ_HOST") or DEFAULT_RABBITMQ_HOST,
            rabbitmq_port=_read(env, "RABBITMQ_PORT") or DEFAULT_RABBITMQ_PORT,
            rabbitmq_user=_read(env, "RABBITMQ_USER"),
            rabbitmq_password=_read(env, "RABBITMQ_PASSWORD"),
        )

    def secrets(self) -> tuple[str, ...]:
        """Values that must never appear in a tool result."""
        return tuple(s for s in (self.control_token, self.rabbitmq_password) if s)

    def token(self) -> str:
        if self.control_token is None:
            raise not_configured("SIM_CONTROL_TOKEN")
        return self.control_token

    def broker_params(self) -> pika.ConnectionParameters:
        user, password = self.rabbitmq_user, self.rabbitmq_password
        if user is None or password is None:
            missing = [
                name
                for name, value in (("RABBITMQ_USER", user), ("RABBITMQ_PASSWORD", password))
                if value is None
            ]
            raise not_configured(*missing)
        if not self.rabbitmq_port.isdigit() or not 0 < int(self.rabbitmq_port) < 65536:
            raise ToolFailure("not_configured", "not configured: RABBITMQ_PORT is not a port")
        return pika.ConnectionParameters(
            host=self.rabbitmq_host,
            port=int(self.rabbitmq_port),
            credentials=pika.PlainCredentials(user, password),
            connection_attempts=1,
            socket_timeout=BROKER_TIMEOUT_S,
            stack_timeout=BROKER_TIMEOUT_S * 2,
            blocked_connection_timeout=BROKER_TIMEOUT_S * 2,
            client_properties={"connection_name": "skyhaul-mcp-sim"},
        )
