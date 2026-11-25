"""The contract's ``Command`` shape, shared by the API (responses) and the ingestor
(``command.updated`` events), and the timeouts of the command lifecycle (m2-design section 6).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from skyhaul_fleet_core.models import Command

COMMAND_TIMEOUT_S: Final = 10
"""A SENT command without a reply for this long becomes TIMED_OUT."""

PENDING_TIMEOUT_S: Final = 30
"""A command still PENDING after this long never reached the broker: FAILED."""

COMMAND_UPDATED: Final = "command.updated"


def command_json(command: Command) -> dict[str, Any]:
    return {
        "id": str(command.id),
        "drone_id": command.drone_id,
        "mission_id": str(command.mission_id) if command.mission_id else None,
        "type": command.type,
        "payload": command.payload,
        "correlation_id": str(command.correlation_id),
        "status": command.status.value,
        "nack_reason": command.nack_reason,
        "nack_detail": command.nack_detail,
        "late": bool(command.late),
        "requested_by": str(command.requested_by),
        "created_at": _iso(command.created_at),
        "sent_at": _iso(command.sent_at),
        "replied_at": _iso(command.replied_at),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
