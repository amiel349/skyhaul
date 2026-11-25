"""The drone registry. Ids are global and never reused: delete is soft, and registering an
id that exists, deleted or not, is a 409. The live ``state`` is null until #27 merges it in
from Redis."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from flask import Response, jsonify
from flask_smorest import Blueprint
from marshmallow import RAISE, Schema, fields, validate
from psycopg.errors import UniqueViolation
from skyhaul_contracts.topology import DRONE_ID_RE
from skyhaul_fleet_core.models import Drone
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
from skyhaul_api.validation import load_body, page_args

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


def drone_json(drone: Drone) -> dict[str, Any]:
    return {
        "id": drone.id,
        "name": drone.name,
        "home": {"lat": drone.home_lat, "lon": drone.home_lon},
        "created_at": drone.created_at.isoformat(),
        "state": None,
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
    return jsonify(items=[drone_json(d) for d in page.items], next_cursor=page.next_cursor)


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
    return jsonify(drone_json(_live_drone(drone_id)))


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
    drone = _live_drone(drone_id)
    session = db()
    drone.deleted_at = datetime.now(UTC)
    _audit(session, "drone.delete", drone.id)
    session.commit()
    return Response(status=204)


def _live_drone(drone_id: str) -> Drone:
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
