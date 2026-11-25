"""Alerts: drone ALERT messages and the derived LINK_LOST, listed newest first, and their
acknowledgement (who and when; idempotent)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from flask import Response, jsonify, request
from flask_smorest import Blueprint
from skyhaul_fleet_core.missions import ALERT_UPDATED, alert_json
from skyhaul_fleet_core.models import Alert
from sqlalchemy import select

from skyhaul_api import events
from skyhaul_api.audit.service import record
from skyhaul_api.auth.guard import principal
from skyhaul_api.auth.permissions import Permission, require
from skyhaul_api.database import db
from skyhaul_api.errors import ApiError
from skyhaul_api.openapi import (
    PROTECTED_ERRORS,
    REQUEST_ID_HEADERS,
    REQUEST_ID_PARAMETER,
    error_doc,
    schema_ref,
)
from skyhaul_api.pagination import paginate
from skyhaul_api.validation import bool_arg, page_args

blp = Blueprint("alerts", __name__)

ALERT_READ = {"x-permission": Permission.ALERT_READ.value}
ALERT_ACK = {"x-permission": Permission.ALERT_ACK.value}


@blp.route("/alerts", methods=["GET"])
@blp.doc(
    operationId="listAlerts",
    summary="List alerts, newest first",
    parameters=[REQUEST_ID_PARAMETER, "OpenQuery", "DroneIdQuery", "Limit", "Cursor"],
    responses=error_doc(*PROTECTED_ERRORS),
    **ALERT_READ,
)
@blp.response(200, schema_ref("AlertPage"), description="OK", headers=REQUEST_ID_HEADERS)
@require(Permission.ALERT_READ)
def list_alerts() -> Response:
    limit, cursor = page_args()
    statement = select(Alert)
    is_open = bool_arg("open")
    if is_open is not None:
        cleared = Alert.cleared_at.is_(None) if is_open else Alert.cleared_at.is_not(None)
        statement = statement.where(cleared)
    drone_id = request.args.get("drone_id")
    if drone_id is not None:
        statement = statement.where(Alert.drone_id == drone_id)
    page = paginate(
        db(),
        statement,
        ts_column=Alert.raised_at,
        id_column=Alert.id,
        id_type=uuid.UUID,
        limit=limit,
        cursor=cursor,
    )
    return jsonify(items=[alert_json(a) for a in page.items], next_cursor=page.next_cursor)


@blp.route("/alerts/<uuid:alert_id>/ack", methods=["POST"])
@blp.doc(
    operationId="ackAlert",
    summary="Acknowledge an alert (idempotent)",
    parameters=[REQUEST_ID_PARAMETER],
    responses=error_doc(*PROTECTED_ERRORS, 404),
    **ALERT_ACK,
)
@blp.response(200, schema_ref("Alert"), description="OK", headers=REQUEST_ID_HEADERS)
@require(Permission.ALERT_ACK)
def ack_alert(alert_id: uuid.UUID) -> Response:
    session = db()
    alert = session.get(Alert, alert_id, with_for_update=True)
    if alert is None:
        raise ApiError(404, "not_found", "The alert does not exist.")
    if alert.acknowledged_at is None:
        user_id = principal().user.id
        alert.acknowledged_by = user_id
        alert.acknowledged_at = datetime.now(UTC)
        record(
            session,
            action="alert.ack",
            outcome="success",
            actor=user_id,
            target_type="alert",
            target_id=str(alert.id),
            data={"drone_id": alert.drone_id, "code": alert.code.value},
        )
        session.commit()
        events.push(ALERT_UPDATED, alert_json(alert))
    return jsonify(alert_json(alert))
