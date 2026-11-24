"""Simulator configuration, from environment variables only.

Secrets (`RABBITMQ_USER`, `RABBITMQ_PASSWORD`) have no defaults: a missing one stops the
process at startup with a clear error. The control API settings arrive with #9.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from skyhaul_contracts.topology import DRONE_ID_RE

_TRUE: Final = frozenset({"1", "true", "yes"})
_FALSE: Final = frozenset({"0", "false", "no", ""})
_SPLIT: Final = re.compile(r"[,\s]+")


class ConfigError(ValueError):
    """The environment does not describe a runnable simulator. The message names the
    variable, never its value."""


@dataclass(frozen=True, slots=True)
class SimConfig:
    rabbitmq_host: str
    rabbitmq_user: str
    rabbitmq_password: str = field(repr=False)
    rabbitmq_port: int = 5672
    rabbitmq_vhost: str = "/"
    rabbitmq_heartbeat_s: int = 30
    connect_timeout_s: float = 30.0
    """How long to keep retrying the broker at startup (and after a lost consumer
    connection) before exiting non-zero."""
    tick_s: float = 0.1
    outbox_max: int = 1000
    prefetch: int = 32
    drain_s: float = 5.0
    """How long the publisher drains the outbox on shutdown before dropping the rest."""
    debug_tap: bool = False
    drones: tuple[str, ...] = ()
    """PLACEHOLDER until #9's spawn: drone ids that exist from startup (`SIM_DRONES`)."""
    seed: int = 0
    """Seeds each drone's own RNG (with its id), so runs repeat (`SIM_SEED`)."""
    geofence_radius_m: float = 5000.0
    home_lat: float = 32.0
    home_lon: float = 34.8
    """Where drones spawn (`SIM_HOME_LAT`, `SIM_HOME_LON`), the centre of their geofence."""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SimConfig:
        env = os.environ if env is None else env
        return cls(
            rabbitmq_host=_required(env, "RABBITMQ_HOST"),
            rabbitmq_user=_required(env, "RABBITMQ_USER"),
            rabbitmq_password=_required(env, "RABBITMQ_PASSWORD"),
            rabbitmq_port=_int(env, "RABBITMQ_PORT", 5672, low=1, high=65535),
            rabbitmq_vhost=env.get("RABBITMQ_VHOST", "/") or "/",
            rabbitmq_heartbeat_s=_int(env, "RABBITMQ_HEARTBEAT_S", 30, low=1, high=600),
            connect_timeout_s=_float(env, "SIM_CONNECT_TIMEOUT_S", 30.0, low=0.0, high=600.0),
            tick_s=_float(env, "SIM_TICK_S", 0.1, low=0.01, high=1.0),
            outbox_max=_int(env, "SIM_OUTBOX_MAX", 1000, low=1, high=100_000),
            prefetch=_int(env, "SIM_PREFETCH", 32, low=1, high=1000),
            drain_s=_float(env, "SIM_DRAIN_S", 5.0, low=0.0, high=5.0),
            debug_tap=_bool(env, "SIM_DEBUG_TAP"),
            drones=_drones(env, "SIM_DRONES"),
            seed=_int(env, "SIM_SEED", 0, low=0, high=2**63 - 1),
            geofence_radius_m=_float(
                env, "SIM_GEOFENCE_RADIUS_M", 5000.0, low=100.0, high=50_000.0
            ),
            # The flat-earth step stays well away from the poles and the antimeridian.
            home_lat=_float(env, "SIM_HOME_LAT", 32.0, low=-80.0, high=80.0),
            home_lon=_float(env, "SIM_HOME_LON", 34.8, low=-179.0, high=179.0),
        )


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value:
        raise ConfigError(f"{name} must be set")
    return value


def _int(env: Mapping[str, str], name: str, default: int, *, low: int, high: int) -> int:
    raw = env.get(name, "")
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer") from None
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}")
    return value


def _float(env: Mapping[str, str], name: str, default: float, *, low: float, high: float) -> float:
    raw = env.get(name, "")
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number") from None
    if not low <= value <= high:  # also rejects NaN
        raise ConfigError(f"{name} must be between {low} and {high}")
    return value


def _bool(env: Mapping[str, str], name: str) -> bool:
    raw = env.get(name, "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ConfigError(f"{name} must be 0 or 1")


def _drones(env: Mapping[str, str], name: str) -> tuple[str, ...]:
    ids = tuple(part for part in _SPLIT.split(env.get(name, "")) if part)
    if not all(DRONE_ID_RE.fullmatch(drone_id) for drone_id in ids):
        raise ConfigError(f"{name} must list valid drone ids")
    return tuple(dict.fromkeys(ids))
