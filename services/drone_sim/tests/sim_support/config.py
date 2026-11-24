"""Simulator configs for tests: fresh secrets per run, and an ephemeral control API port."""

import secrets
from dataclasses import replace
from typing import Any

from skyhaul_drone_sim.config import SimConfig

_BASE = SimConfig(
    rabbitmq_host="unused",
    rabbitmq_user="unused",
    rabbitmq_password=secrets.token_hex(8),
    control_token=secrets.token_hex(32),
    control_port=0,  # the OS picks a free port, so tests never collide
)


def sim_config(**changes: Any) -> SimConfig:
    """A config for an in-process simulator on fake connections."""
    return replace(_BASE, **changes)


def always_current(namespace: str, epoch: int) -> bool:
    """An `is_current` for publisher tests that are not about reset epochs."""
    return True
