"""Missions: plan, launch (ARM and START_MISSION through the command service), cancel, abort,
and read them back with their history. One live mission per drone (409 otherwise)."""

from __future__ import annotations

import uuid
from typing import Any

from flask import Response, jsonify, request
from flask_smorest import Blueprint
from marshmallow import RAISE, Schema, fields, validate
from skyhaul_contracts.topology import DRONE_ID_RE
from skyhaul_fleet_core.commands import command_json
from skyhaul_fleet_core.missions import mission_event_json, mission_json
from skyhaul_fleet_core.models import Command, Mission, MissionEvent, MissionState
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from skyhaul_api.auth.guard import principal
from skyhaul_api.auth.permissions import Permission, require
from skyhaul_api.database import db
from skyhaul_api.drones.routes import GeoPointSchema, live_drone
from skyhaul_api.errors import ApiError
from skyhaul_api.logs import request_id
from skyhaul_api.missions import service
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
from skyhaul_api.validation import idempotency_key, load_body, page_args

blp = Blueprint("missions", __name__)

MISSION_READ = {"x-permission": Permission.MISSION_READ.value}
MISSION_WRITE = {"x-permission": Permission.MISSION_WRITE.value}
MAX_WAYPOINTS = 50


class WaypointSchema(GeoPointSchema):
    altitude_m = fields.Float(required=True, validate=validate.Range(5, 120), allow_nan=False)


class MissionCreateSchema(Schema):
    class Meta:
        unknown = RAISE

    drone_id = fields.String(
        required=True,
        validate=validate.Regexp(rf"^{DRONE_ID_RE.pattern}\Z", error="Not a drone id."),
    )
    waypoints = fields.List(
        fields.Nested(WaypointSchema),
        required=True,
        validate=validate.Length(min=1, max=MAX_WAYPOINTS),
    )
    start = fields.Boolean(load_default=False)


class MissionAbortSchema(Schema):
    class Meta:
        unknown = RAISE

    action = fields.String(required=True, validate=validate.OneOf(service.ABORT_ACTIONS))


@blp.route("/missions", methods=["POST"])
@blp.doc(
    operationId="createMission",
    summary="Plan a mission, and with start=true launch it",
    parameters=[REQUEST_ID_PARAMETER, "IdempotencyKey"],
    requestBody=json_body("MissionCreate"),
    responses=error_doc(*PROTECTED_ERRORS, *BODY_ERRORS, 404, 409, 503),
    **MISSION_WRITE,
)
@blp.response(
    201,
    schema_ref("MissionDispatch"),
    description=(
        "Created (DISPATCHING when start=true, else PLANNED), or an Idempotency-Key replay"
    ),
    headers=REQUEST_ID_HEADERS,
)
@require(Permission.MISSION_WRITE)
def create_mission() -> Response:
    body = load_body(MissionCreateSchema())
    drone = live_drone(body["drone_id"])
    key = idempotency_key()
    user_id = principal().user.id
    session = db()
    waypoints = body["waypoints"]
    if key is not None and (existing := service.find_by_key(session, user_id, key)) is not None:
        return _dispatch(201, *service.replay(session, existing, drone.id, waypoints))
    try:
        mission = service.create(
            session,
            drone_id=drone.id,
            waypoints=waypoints,
            start=body["start"],
            user_id=user_id,
            correlation_id=request_id(),
            idempotency_key=key,
        )
    except IntegrityError as exc:
        session.rollback()
        if service.is_live_conflict(exc):
            raise service.mission_live() from None
        # A concurrent request with the same key won the insert.
        existing = service.find_by_key(session, user_id, key) if key else None
        if existing is None:
            raise
        return _dispatch(201, *service.replay(session, existing, drone.id, waypoints))
    sent = service.launch(session, mission, user_id) if body["start"] else []
    return _dispatch(201, service.pushed(mission), sent)


@blp.route("/missions", methods=["GET"])
@blp.doc(
    operationId="listMissions",
    summary="List missions, newest first",
    parameters=[REQUEST_ID_PARAMETER, "MissionStateQuery", "DroneIdQuery", "Limit", "Cursor"],
    responses=error_doc(*PROTECTED_ERRORS),
    **MISSION_READ,
)
@blp.response(200, schema_ref("MissionPage"), description="OK", headers=REQUEST_ID_HEADERS)
@require(Permission.MISSION_READ)
def list_missions() -> Response:
    limit, cursor = page_args()
    statement = select(Mission)
    state = request.args.get("state")
    if state is not None:
        if state not in MissionState.__members__:
            raise ApiError(400, "bad_request", "state must be a mission state.")
        statement = statement.where(Mission.state == MissionState(state))
    drone_id = request.args.get("drone_id")
    if drone_id is not None:
        statement = statement.where(Mission.drone_id == drone_id)
    page = paginate(
        db(),
        statement,
        ts_column=Mission.created_at,
        id_column=Mission.id,
        id_type=uuid.UUID,
        limit=limit,
        cursor=cursor,
    )
    return jsonify(items=[mission_json(m) for m in page.items], next_cursor=page.next_cursor)


@blp.route("/missions/<uuid:mission_id>", methods=["GET"])
@blp.doc(
    operationId="getMission",
    summary="One mission",
    parameters=[REQUEST_ID_PARAMETER],
    responses=error_doc(*PROTECTED_ERRORS, 404),
    **MISSION_READ,
)
@blp.response(200, schema_ref("Mission"), description="OK", headers=REQUEST_ID_HEADERS)
@require(Permission.MISSION_READ)
def get_mission(mission_id: uuid.UUID) -> Response:
    return jsonify(mission_json(service.find(db(), mission_id)))


@blp.route("/missions/<uuid:mission_id>/events", methods=["GET"])
@blp.doc(
    operationId="listMissionEvents",
    summary="Mission history, oldest first",
    parameters=[REQUEST_ID_PARAMETER, "Limit", "Cursor"],
    responses=error_doc(*PROTECTED_ERRORS, 404),
    **MISSION_READ,
)
@blp.response(200, schema_ref("MissionEventPage"), description="OK", headers=REQUEST_ID_HEADERS)
@require(Permission.MISSION_READ)
def list_mission_events(mission_id: uuid.UUID) -> Response:
    session = db()
    mission = service.find(session, mission_id)
    limit, cursor = page_args()
    page = paginate(
        session,
        select(MissionEvent).where(MissionEvent.mission_id == mission.id),
        ts_column=MissionEvent.ts,
        id_column=MissionEvent.id,
        id_type=int,
        limit=limit,
        cursor=cursor,
        oldest_first=True,
    )
    return jsonify(items=[mission_event_json(e) for e in page.items], next_cursor=page.next_cursor)


@blp.route("/missions/<uuid:mission_id>/start", methods=["POST"])
@blp.doc(
    operationId="startMission",
    summary="Launch a PLANNED mission",
    parameters=[REQUEST_ID_PARAMETER],
    responses=error_doc(*PROTECTED_ERRORS, 404, 409, 503),
    **MISSION_WRITE,
)
@blp.response(
    202, schema_ref("MissionDispatch"), description="Dispatching", headers=REQUEST_ID_HEADERS
)
@require(Permission.MISSION_WRITE)
def start_mission(mission_id: uuid.UUID) -> Response:
    session = db()
    mission = service.find(session, mission_id)
    user_id = principal().user.id
    service.start(session, mission, user_id)
    sent = service.launch(session, mission, user_id)
    return _dispatch(202, service.pushed(mission), sent)


@blp.route("/missions/<uuid:mission_id>/abort", methods=["POST"])
@blp.doc(
    operationId="abortMission",
    summary=(
        "Abort a DISPATCHING or ACTIVE mission; ABORTED once the drone reports its final status"
    ),
    parameters=[REQUEST_ID_PARAMETER],
    requestBody=json_body("MissionAbort"),
    responses=error_doc(*PROTECTED_ERRORS, *BODY_ERRORS, 404, 409, 503),
    **MISSION_WRITE,
)
@blp.response(
    202, schema_ref("MissionDispatch"), description="Abort command sent", headers=REQUEST_ID_HEADERS
)
@require(Permission.MISSION_WRITE)
def abort_mission(mission_id: uuid.UUID) -> Response:
    session = db()
    mission = service.find(session, mission_id)
    body = load_body(MissionAbortSchema())
    command = service.abort(session, mission, principal().user.id, body["action"])
    session.refresh(mission)
    return _dispatch(202, mission, [command])


@blp.route("/missions/<uuid:mission_id>/cancel", methods=["POST"])
@blp.doc(
    operationId="cancelMission",
    summary="Cancel a PLANNED mission",
    parameters=[REQUEST_ID_PARAMETER],
    responses=error_doc(*PROTECTED_ERRORS, 404, 409),
    **MISSION_WRITE,
)
@blp.response(200, schema_ref("Mission"), description="OK", headers=REQUEST_ID_HEADERS)
@require(Permission.MISSION_WRITE)
def cancel_mission(mission_id: uuid.UUID) -> Response:
    session = db()
    mission = service.find(session, mission_id)
    service.cancel(session, mission, principal().user.id)
    return jsonify(mission_json(service.pushed(mission)))


def _dispatch(status: int, mission: Mission, sent: list[Command]) -> Response:
    body: dict[str, Any] = {
        "mission": mission_json(mission),
        "commands": [command_json(c) for c in sent],
    }
    response = jsonify(body)
    response.status_code = status
    return response
