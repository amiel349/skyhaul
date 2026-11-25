"""Redis key and channel names. Every service builds them here, nowhere else."""

from __future__ import annotations

from typing import Final

from skyhaul_contracts.topology import DRONE_ID_RE

EVENTS_CHANNEL: Final = "events"
"""Pub/sub channel carrying ``{"event": ..., "data": ...}`` to the Socket.IO fan-out."""


DRONE_STATE_PREFIX: Final = "drone:"
DRONE_STATE_PATTERN: Final = f"{DRONE_STATE_PREFIX}*"
"""SCAN pattern of every ``drone:<id>`` hash (the link watchdog seeds from it)."""


def drone_state_key(drone_id: str) -> str:
    """Hash with the latest known state of one drone: ``drone:<id>``."""
    if not DRONE_ID_RE.fullmatch(drone_id):
        raise ValueError("drone_id is not a valid DroneId")
    return f"{DRONE_STATE_PREFIX}{drone_id}"
