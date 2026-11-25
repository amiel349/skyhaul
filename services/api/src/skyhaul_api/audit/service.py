"""Write audit entries. ``data`` holds ids and reasons only: never passwords, tokens or headers."""

from __future__ import annotations

import uuid
from typing import Any

from flask import has_request_context, request
from skyhaul_fleet_core.models import AuditEntry
from sqlalchemy.orm import Session

from skyhaul_api.database import new_session
from skyhaul_api.logs import request_id


def record(
    session: Session,
    *,
    action: str,
    outcome: str,
    actor: uuid.UUID | None,
    target_type: str | None = None,
    target_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Add an entry to ``session``; the caller commits it with its own change."""
    entry_data = dict(data or {})
    ip: str | None = None
    if has_request_context():
        entry_data["request_id"] = request_id()
        ip = request.remote_addr
    session.add(
        AuditEntry(
            actor_user_id=actor,
            action=action,
            outcome=outcome,
            target_type=target_type,
            target_id=target_id,
            ip=ip,
            data=entry_data,
        )
    )


def record_now(**kwargs: Any) -> None:
    """Record and commit in a session of its own, for requests that end in an error."""
    with new_session() as session:
        record(session, **kwargs)
        session.commit()
