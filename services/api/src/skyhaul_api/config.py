"""API settings, from environment variables only. Secrets have no defaults."""

from __future__ import annotations

from dataclasses import dataclass, field

from skyhaul_fleet_core.settings import require, require_secret

MAX_BODY_BYTES = 64 * 1024


@dataclass(frozen=True)
class Config:
    # The URLs carry passwords, so they stay out of repr() and tracebacks.
    database_url: str = field(repr=False)
    redis_url: str = field(repr=False)
    jwt_secret: str = field(repr=False)


def load_config() -> Config:
    """Read the settings. Raises ``SettingsError`` naming the first bad variable."""
    return Config(
        database_url=require("DATABASE_URL"),
        redis_url=require("REDIS_URL"),
        jwt_secret=require_secret("JWT_SECRET", min_length=32),
    )
