"""Ingestor settings, from environment variables only. Secrets have no defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from skyhaul_fleet_core.settings import optional_int, require


@dataclass(frozen=True)
class IngestorConfig:
    database_url: str = field(repr=False)
    redis_url: str = field(repr=False)
    rabbitmq_host: str
    rabbitmq_user: str
    rabbitmq_password: str = field(repr=False)
    rabbitmq_port: int = 5672
    rabbitmq_vhost: str = "/"
    retention_days: int = 7
    # Dev stack only, until broker-init (#29) declares the topology. Off by default: the
    # ingestor declares nothing and exits if its queue is missing.
    declare_queue: bool = False
    alive_file: str = "/tmp/skyhaul-ingestor.alive"  # noqa: S108 - liveness marker, no data


def load_config() -> IngestorConfig:
    return IngestorConfig(
        database_url=require("DATABASE_URL"),
        redis_url=require("REDIS_URL"),
        rabbitmq_host=require("RABBITMQ_HOST"),
        rabbitmq_user=require("RABBITMQ_USER"),
        rabbitmq_password=require("RABBITMQ_PASSWORD"),
        rabbitmq_port=optional_int("RABBITMQ_PORT", 5672),
        rabbitmq_vhost=os.environ.get("RABBITMQ_VHOST", "") or "/",
        retention_days=optional_int("TELEMETRY_RETENTION_DAYS", 7),
        declare_queue=os.environ.get("INGESTOR_DECLARE_QUEUE", "0") == "1",
    )
