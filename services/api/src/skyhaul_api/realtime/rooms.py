"""Socket.IO rooms (ADR 0014). The server picks every room; clients cannot name one.

A socket joins ``fleet`` and ``role:<role>``. The room of an event comes from the event name
alone, never from its payload, so a payload field cannot redirect an emit.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Final

from skyhaul_fleet_core.commands import COMMAND_UPDATED
from skyhaul_fleet_core.models import Role

log = logging.getLogger(__name__)

FLEET_ROOM: Final = "fleet"


def role_room(role: Role) -> str:
    return f"role:{role.value}"


def rooms_for(role: Role) -> list[str]:
    """The rooms a socket of this role joins at connect."""
    return [FLEET_ROOM, role_room(role)]


EVENT_ROOMS: Final[dict[str, tuple[str, ...]]] = {
    "drone.state": (FLEET_ROOM,),
    "mission.updated": (FLEET_ROOM,),
    "alert.raised": (FLEET_ROOM,),
    "alert.updated": (FLEET_ROOM,),
    # Only roles that may send commands see their replies.
    COMMAND_UPDATED: (role_room(Role.DISPATCHER), role_room(Role.ADMIN)),
}


def route(raw: bytes | str) -> tuple[str, dict[str, Any], tuple[str, ...]] | None:
    """``(event, data, rooms)`` for a ``{"event", "data"}`` message from Redis, or ``None``
    (logged) for anything malformed or an unknown event name."""
    try:
        message = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        log.warning("dropped an undecodable event")
        return None
    if not isinstance(message, dict) or set(message) != {"event", "data"}:
        log.warning("dropped an event without exactly event and data")
        return None
    event, data = message["event"], message["data"]
    rooms = EVENT_ROOMS.get(event) if isinstance(event, str) else None
    if rooms is None or not isinstance(data, dict):
        log.warning("dropped an unknown or malformed event")
        return None
    return event, data, rooms
