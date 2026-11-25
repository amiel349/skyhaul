"""``GET /audit``: the audit log, newest first. Admin only (``audit:read``)."""

from __future__ import annotations

from typing import Any

from flask import Response, jsonify
from flask_smorest import Blueprint
from skyhaul_fleet_core.models import AuditEntry
from sqlalchemy import select

from skyhaul_api.auth.permissions import Permission, require
from skyhaul_api.database import db
from skyhaul_api.openapi import (
    PROTECTED_ERRORS,
    REQUEST_ID_HEADERS,
    REQUEST_ID_PARAMETER,
    error_doc,
    schema_ref,
)
from skyhaul_api.pagination import paginate
from skyhaul_api.validation import page_args, time_arg

blp = Blueprint("audit", __name__)


def audit_json(entry: AuditEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "actor_user_id": str(entry.actor_user_id) if entry.actor_user_id else None,
        "action": entry.action,
        "target_type": entry.target_type,
        "target_id": entry.target_id,
        "outcome": entry.outcome,
        "ip": str(entry.ip) if entry.ip is not None else None,
        "ts": entry.ts.isoformat(),
        "data": entry.data,
    }


@blp.route("/audit", methods=["GET"])
@blp.doc(
    operationId="listAudit",
    summary="Audit log, newest first",
    parameters=[REQUEST_ID_PARAMETER, "Since", "Until", "Limit", "Cursor"],
    responses=error_doc(*PROTECTED_ERRORS),
    **{"x-permission": Permission.AUDIT_READ.value},
)
@blp.response(200, schema_ref("AuditEntryPage"), description="OK", headers=REQUEST_ID_HEADERS)
@require(Permission.AUDIT_READ)
def list_audit() -> Response:
    limit, cursor = page_args()
    statement = select(AuditEntry)
    since, until = time_arg("since"), time_arg("until")
    if since is not None:
        statement = statement.where(AuditEntry.ts >= since)
    if until is not None:
        statement = statement.where(AuditEntry.ts < until)
    page = paginate(
        db(),
        statement,
        ts_column=AuditEntry.ts,
        id_column=AuditEntry.id,
        id_type=int,
        limit=limit,
        cursor=cursor,
    )
    return jsonify(items=[audit_json(e) for e in page.items], next_cursor=page.next_cursor)
