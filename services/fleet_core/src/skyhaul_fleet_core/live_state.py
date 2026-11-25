"""The Redis latest-state hash ``drone:{id}``: field names, groups and the reader.

The ingestor writes it (a newer ``ts`` wins, per field group) and the API reads it. Every
value is stored JSON-encoded, so ``null``, numbers and the ``target`` object round-trip.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final

STATE_TTL_S: Final = 24 * 3600

# A group is overwritten only by a message with a newer ts than its ``<group>_ts`` field.
GROUPS: Final[Mapping[str, tuple[str, ...]]] = {
    "hb": ("flight_state", "armed", "last_heartbeat_at"),
    "pos": ("lat", "lon", "altitude_m", "heading_deg", "ground_speed_mps", "target"),
    "bat": ("battery_pct", "est_remaining_s"),
    "mis": ("mission_id",),
}

_POSITION: Final = ("lat", "lon", "altitude_m", "heading_deg", "ground_speed_mps")


def read_live_state(raw: Mapping[Any, Any]) -> dict[str, Any] | None:
    """A ``DroneLiveState`` from an HGETALL result, or None when the drone has no state."""
    text = {_text(k): _text(v) for k, v in raw.items()}
    if not text:
        return None
    position = None
    if all(name in text for name in _POSITION):
        position = {name: _field(text, name) for name in _POSITION}
    return {
        "flight_state": _field(text, "flight_state"),
        "armed": _field(text, "armed"),
        "battery_pct": _field(text, "battery_pct"),
        "est_remaining_s": _field(text, "est_remaining_s"),
        "position": position,
        "target": _field(text, "target"),
        "mission_id": _field(text, "mission_id"),
        "link": _field(text, "link") or "UP",
        "last_heartbeat_at": _field(text, "last_heartbeat_at"),
        "last_seen_at": _field(text, "last_seen_at"),
        "ts": _field(text, "ts"),
    }


def _field(text: Mapping[str, str], name: str) -> Any:
    value = text.get(name)
    return None if value is None else json.loads(value)


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value
