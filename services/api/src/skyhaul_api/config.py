"""API settings, from environment variables only. Secrets have no defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from skyhaul_fleet_core.settings import optional_int, require, require_secret

MAX_BODY_BYTES = 64 * 1024


@dataclass(frozen=True)
class Config:
    # The URLs carry passwords, so they stay out of repr() and tracebacks.
    database_url: str = field(repr=False)
    redis_url: str = field(repr=False)
    jwt_secret: str = field(repr=False)
    rabbitmq_host: str
    rabbitmq_user: str
    rabbitmq_password: str = field(repr=False)
    rabbitmq_port: int = 5672
    rabbitmq_vhost: str = "/"


def load_config() -> Config:
    """Read the settings. Raises ``SettingsError`` naming the first bad variable."""
    return Config(
        database_url=require("DATABASE_URL"),
        redis_url=require("REDIS_URL"),
        jwt_secret=require_secret("JWT_SECRET", min_length=32),
        rabbitmq_host=require("RABBITMQ_HOST"),
        rabbitmq_user=require("RABBITMQ_USER"),
        rabbitmq_password=require("RABBITMQ_PASSWORD"),
        rabbitmq_port=optional_int("RABBITMQ_PORT", 5672),
        rabbitmq_vhost=os.environ.get("RABBITMQ_VHOST", "") or "/",
    )
