"""The drone registry and its stored telemetry. Ids are global and never reused: delete is
soft, and registering an id that exists, deleted or not, is a 409. The live ``state`` comes
from the ingestor's Redis hash ``drone:{id}``; it is null when there is none or Redis is down."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import redis
from flask import Response, current_app, jsonify, request
from flask_smorest import Blueprint
from marshmallow import RAISE, Schema, fields, validate
from psycopg.errors import UniqueViolation
from skyhaul_contracts.message import format_ts
from skyhaul_contracts.topology import DRONE_ID_RE
from skyhaul_fleet_core.keys import drone_state_key
from skyhaul_fleet_core.live_state import read_live_state
from skyhaul_fleet_core.models import Drone, Telemetry
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from skyhaul_api.audit.service import record
from skyhaul_api.auth.guard import principal
from skyhaul_api.auth.permissions import Permission, require
from skyhaul_api.database import db
from skyhaul_api.errors import ApiError
from skyhaul_api.openapi import (
    BODY_ERRORS,
    PROTECTED_ERRORS,
    REQUEST_ID_HEADERS,
    REQUEST_ID_PARAMETER,
    error_doc,
    json_body,
    schema_ref,
)
from skyhaul_api.pagination import paginate
from skyhaul_api.validation import load_body, page_args, time_arg

log = logging.getLogger(__name__)

blp = Blueprint("drones", __name__)

FLEET_READ = {"x-permission": Permission.FLEET_READ.value}
DRONE_ADMIN = {"x-permission": Permission.DRONE_ADMIN.value}


class GeoPointSchema(Schema):
    class Meta:
        unknown = RAISE

    lat = fields.Float(required=True, validate=validate.Range(-90, 90), allow_nan=False)
    lon = fields.Float(
        required=True, validate=validate.Range(-180, 180, max_inclusive=False), allow_nan=False
    )


class DroneCreateSchema(Schema):
    class Meta:
        unknown = RAISE

    id = fields.String(
        required=True,
        validate=validate.Regexp(rf"^{DRONE_ID_RE.pattern}\Z", error="Not a drone id."),
    )
    name = fields.String(required=True, validate=validate.Length(min=1, max=128))
    home = fields.Nested(GeoPointSchema, required=True)


# The contract's TelemetryType: HEARTBEAT is not stored, ACK and NACK update commands.
STORED_TYPES = frozenset({"POSITION", "BATTERY", "MISSION_STATUS", "ALERT"})


def drone_json(drone: Drone, state: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": drone.id,
        "name": drone.name,
        "home": {"lat": drone.home_lat, "lon": drone.home_lon},
        "created_at": drone.created_at.isoformat(),
        "state": state,
    }


def live_states(drone_ids: Sequence[str]) -> list[dict[str, Any] | None]:
    """The Redis latest state of each drone, in one round trip. All None if Redis is down."""
    if not drone_ids:
        return []
    client: redis.Redis = current_app.extensions["skyhaul.redis"]
    pipe = client.pipeline(transaction=False)
    for drone_id in drone_ids:
        pipe.hgetall(drone_state_key(drone_id))
    try:
        return [read_live_state(raw) for raw in pipe.execute()]
    except (redis.RedisError, OSError) as exc:
        log.warning("latest state unavailable (%s)", type(exc).__name__)
        return [None] * len(drone_ids)


def telemetry_json(row: Telemetry) -> dict[str, Any]:
    return {
        "msg_id": str(row.msg_id),
        "drone_id": row.drone_id,
        "type": row.type,
        "ts": format_ts(row.ts),
        "received_at": row.received_at.isoformat(),
        "correlation_id": str(row.correlation_id) if row.correlation_id else None,
        "payload": row.payload,
    }


@blp.route("/drones", methods=["GET"])
@blp.doc(
    operationId="listDrones",
    summary="List drones with their latest state",
    parameters=[REQUEST_ID_PARAMETER, "Limit", "Cursor"],
    responses=error_doc(*PROTECTED_ERRORS),
    **FLEET_READ,
)
@blp.response(200, schema_ref("DronePage"), description="OK", headers=REQUEST_ID_HEADERS)
@require(Permission.FLEET_READ)
def list_drones() -> Response:
    limit, cursor = page_args()
    page = paginate(
        db(),
        select(Drone).where(Drone.deleted_at.is_(None)),
        ts_column=Drone.created_at,
        id_column=Drone.id,
        id_type=str,
        limit=limit,
        cursor=cursor,
    )
    states = live_states([d.id for d in page.items])
    items = [drone_json(d, state) for d, state in zip(page.items, states, strict=True)]
    return jsonify(items=items, next_cursor=page.next_cursor)


@blp.route("/drones", methods=["POST"])
@blp.doc(
    operationId="createDrone",
    summary="Register a drone",
    parameters=[REQUEST_ID_PARAMETER],
    requestBody=json_body("DroneCreate"),
    responses=error_doc(*PROTECTED_ERRORS, *BODY_ERRORS, 409),
    **DRONE_ADMIN,
)
@blp.response(201, schema_ref("Drone"), description="Created", headers=REQUEST_ID_HEADERS)
@require(Permission.DRONE_ADMIN)
def create_drone() -> Response:
    body = load_body(DroneCreateSchema())
    session = db()
    if session.get(Drone, body["id"]) is not None:
        raise _id_taken()
    drone = Drone(
        id=body["id"], name=body["name"], home_lat=body["home"]["lat"], home_lon=body["home"]["lon"]
    )
    session.add(drone)
    try:
        session.flush()
    except IntegrityError as exc:
        if not isinstance(exc.orig, UniqueViolation):
            raise
        raise _id_taken() from None
    _audit(session, "drone.create", drone.id)
    session.commit()
    response = jsonify(drone_json(drone))
    response.status_code = 201
    return response


@blp.route("/drones/<string:drone_id>", methods=["GET"])
@blp.doc(
    operationId="getDrone",
    summary="One drone with its latest state",
    parameters=[REQUEST_ID_PARAMETER],
    responses=error_doc(*PROTECTED_ERRORS, 404),
    **FLEET_READ,
)
@blp.response(200, schema_ref("Drone"), description="OK", headers=REQUEST_ID_HEADERS)
@require(Permission.FLEET_READ)
def get_drone(drone_id: str) -> Response:
    drone = live_drone(drone_id)
    return jsonify(drone_json(drone, live_states([drone.id])[0]))


@blp.route("/drones/<string:drone_id>/telemetry", methods=["GET"])
@blp.doc(
    operationId="listTelemetry",
    summary="Stored telemetry, newest first",
    parameters=[REQUEST_ID_PARAMETER, "TelemetryTypeQuery", "Since", "Until", "Limit", "Cursor"],
    responses=error_doc(*PROTECTED_ERRORS, 404),
    **FLEET_READ,
)
@blp.response(200, schema_ref("TelemetryRecordPage"), description="OK", headers=REQUEST_ID_HEADERS)
@require(Permission.FLEET_READ)
def list_telemetry(drone_id: str) -> Response:
    drone = live_drone(drone_id)
    limit, cursor = page_args()
    statement = select(Telemetry).where(Telemetry.drone_id == drone.id)
    kind = request.args.get("type")
    if kind is not None:
        if kind not in STORED_TYPES:
            raise ApiError(400, "bad_request", "type must be a stored telemetry type.")
        statement = statement.where(Telemetry.type == kind)
    since, until = time_arg("since"), time_arg("until")
    if since is not None:
        statement = statement.where(Telemetry.ts >= since)
    if until is not None:
        statement = statement.where(Telemetry.ts < until)
    page = paginate(
        db(),
        statement,
        ts_column=Telemetry.ts,
        id_column=Telemetry.msg_id,
        id_type=uuid.UUID,
        limit=limit,
        cursor=cursor,
    )
    return jsonify(items=[telemetry_json(t) for t in page.items], next_cursor=page.next_cursor)


@blp.route("/drones/<string:drone_id>", methods=["DELETE"])
@blp.doc(
    operationId="deleteDrone",
    summary="Soft-delete a drone (its id is never reused)",
    parameters=[REQUEST_ID_PARAMETER],
    responses=error_doc(*PROTECTED_ERRORS, 404, 409),
    **DRONE_ADMIN,
)
@blp.response(204, description="Deleted")
@require(Permission.DRONE_ADMIN)
def delete_drone(drone_id: str) -> Response:
    drone = live_drone(drone_id)
    session = db()
    drone.deleted_at = datetime.now(UTC)
    _audit(session, "drone.delete", drone.id)
    session.commit()
    return Response(status=204)


def live_drone(drone_id: str) -> Drone:
    drone = db().get(Drone, drone_id)
    if drone is None or drone.deleted_at is not None:
        raise ApiError(404, "not_found", "The drone does not exist.")
    return drone


def _audit(session: Any, action: str, drone_id: str) -> None:
    record(
        session,
        action=action,
        outcome="success",
        actor=principal().user.id,
        target_type="drone",
        target_id=drone_id,
    )


def _id_taken() -> ApiError:
    return ApiError(409, "drone_id_taken", "The drone id is registered already.")
