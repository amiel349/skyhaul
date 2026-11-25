"""The contract's ``Mission``, ``MissionEvent`` and ``Alert`` shapes, shared by the API
(responses) and the ingestor (``mission.updated`` and ``alert.*`` events), and the mission
and link timeouts (m2-design section 8).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from skyhaul_fleet_core.models import Alert, Mission, MissionEvent

MISSION_DISPATCH_TIMEOUT_S: Final = 60
"""A DISPATCHING mission without ACTIVE for this long after its launch becomes FAILED."""

LINK_LOSS_AFTER_S: Final = 5
"""A watched drone without a HEARTBEAT for this long gets a derived LINK_LOST alert."""

MISSION_UPDATED: Final = "mission.updated"
ALERT_RAISED: Final = "alert.raised"
ALERT_UPDATED: Final = "alert.updated"

FAILURE_REASON_MAX: Final = 256


def failure_reason(text: str) -> str:
    """Plain text data within the contract's 256 characters."""
    return text[:FAILURE_REASON_MAX]


def mission_json(mission: Mission) -> dict[str, Any]:
    return {
        "id": str(mission.id),
        "drone_id": mission.drone_id,
        "state": mission.state.value,
        "waypoints": mission.waypoints,
        "correlation_id": str(mission.correlation_id),
        "created_by": str(mission.created_by),
        "created_at": _iso(mission.created_at),
        "started_at": _iso(mission.started_at),
        "ended_at": _iso(mission.ended_at),
        "failure_reason": mission.failure_reason,
        "waypoint_index": mission.waypoint_index,
        "progress_pct": mission.progress_pct,
    }


def mission_event_json(event: MissionEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "mission_id": str(event.mission_id),
        "ts": _iso(event.ts),
        "kind": event.kind,
        "data": event.data,
    }


def alert_json(alert: Alert) -> dict[str, Any]:
    return {
        "id": str(alert.id),
        "drone_id": alert.drone_id,
        "code": alert.code.value,
        "severity": alert.severity.value,
        "message": alert.message,
        "value": alert.value,
        "source": alert.source.value,
        "raised_at": _iso(alert.raised_at),
        "acknowledged_by": str(alert.acknowledged_by) if alert.acknowledged_by else None,
        "acknowledged_at": _iso(alert.acknowledged_at),
        "cleared_at": _iso(alert.cleared_at),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
