"""Commands: send one to a drone (202 once the broker confirmed it) and read them back.

START_MISSION is refused here (422): missions go only through ``/missions``. The API never
consumes AMQP; the ingestor applies the drone's ACK or NACK.
"""

from __future__ import annotations

import uuid
from typing import Final

from flask import Response, current_app, jsonify
from flask_smorest import Blueprint
from marshmallow import RAISE, Schema, fields, validate
from psycopg.errors import UniqueViolation
from skyhaul_contracts.codec import Codec
from skyhaul_fleet_core.commands import command_json
from skyhaul_fleet_core.models import Command
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from skyhaul_api.auth.guard import principal
from skyhaul_api.auth.permissions import Permission, require
from skyhaul_api.commands import service
from skyhaul_api.commands.publisher import CommandPublisher
from skyhaul_api.database import db
from skyhaul_api.drones.routes import live_drone
from skyhaul_api.errors import ApiError
from skyhaul_api.logs import request_id
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

blp = Blueprint("commands", __name__)

PUBLISHER_KEY: Final = "skyhaul.publisher"
CODEC_KEY: Final = "skyhaul.codec"

# The contract's DirectCommandType: every command type except START_MISSION.
DIRECT_TYPES: Final = ("ARM", "TAKEOFF", "GOTO", "RETURN_HOME", "LAND", "SET_SPEED")

FLEET_READ = {"x-permission": Permission.FLEET_READ.value}
COMMAND_SEND = {"x-permission": Permission.COMMAND_SEND.value}


class CommandRequestSchema(Schema):
    class Meta:
        unknown = RAISE

    type = fields.String(required=True, validate=validate.OneOf(DIRECT_TYPES))
    payload = fields.Dict(keys=fields.String(), load_default=dict)


@blp.route("/drones/<string:drone_id>/commands", methods=["POST"])
@blp.doc(
    operationId="sendCommand",
    summary="Send a command. 202 means broker-confirmed; the reply arrives as command.updated.",
    parameters=[REQUEST_ID_PARAMETER, "IdempotencyKey"],
    requestBody=json_body("CommandRequest"),
    responses=error_doc(*PROTECTED_ERRORS, *BODY_ERRORS, 404, 409, 503),
    **COMMAND_SEND,
)
@blp.response(
    202,
    schema_ref("Command"),
    description="Accepted and broker-confirmed (status SENT), or an Idempotency-Key replay",
    headers=REQUEST_ID_HEADERS,
)
@require(Permission.COMMAND_SEND)
def send_command(drone_id: str) -> Response:
    drone = live_drone(drone_id)
    body = load_body(CommandRequestSchema())
    key = idempotency_key()
    user_id = principal().user.id
    session = db()
    codec: Codec = current_app.extensions[CODEC_KEY]
    message, wire = service.build_message(
        codec, body["type"], drone.id, body["payload"], request_id()
    )
    if key is not None and (existing := service.find_by_key(session, user_id, key)) is not None:
        return _accepted(service.replay(existing, message))
    try:
        command = service.store(session, message, user_id=user_id, idempotency_key=key)
    except IntegrityError as exc:
        # A concurrent request with the same key won the insert.
        session.rollback()
        if key is None or not isinstance(exc.orig, UniqueViolation):
            raise
        existing = service.find_by_key(session, user_id, key)
        if existing is None:
            raise
        return _accepted(service.replay(existing, message))

    publisher: CommandPublisher = current_app.extensions[PUBLISHER_KEY]
    outcome = service.publish(publisher, message, wire)
    return _accepted(service.finish(session, command, outcome))


@blp.route("/drones/<string:drone_id>/commands", methods=["GET"])
@blp.doc(
    operationId="listDroneCommands",
    summary="Commands sent to a drone, newest first",
    parameters=[REQUEST_ID_PARAMETER, "Limit", "Cursor"],
    responses=error_doc(*PROTECTED_ERRORS, 404),
    **FLEET_READ,
)
@blp.response(200, schema_ref("CommandPage"), description="OK", headers=REQUEST_ID_HEADERS)
@require(Permission.FLEET_READ)
def list_drone_commands(drone_id: str) -> Response:
    drone = live_drone(drone_id)
    limit, cursor = page_args()
    page = paginate(
        db(),
        select(Command).where(Command.drone_id == drone.id),
        ts_column=Command.created_at,
        id_column=Command.id,
        id_type=uuid.UUID,
        limit=limit,
        cursor=cursor,
    )
    return jsonify(items=[command_json(c) for c in page.items], next_cursor=page.next_cursor)


@blp.route("/commands/<uuid:command_id>", methods=["GET"])
@blp.doc(
    operationId="getCommand",
    summary="One command",
    parameters=[REQUEST_ID_PARAMETER],
    responses=error_doc(*PROTECTED_ERRORS, 404),
    **FLEET_READ,
)
@blp.response(200, schema_ref("Command"), description="OK", headers=REQUEST_ID_HEADERS)
@require(Permission.FLEET_READ)
def get_command(command_id: uuid.UUID) -> Response:
    command = db().get(Command, command_id)
    if command is None:
        raise ApiError(404, "not_found", "The command does not exist.")
    return jsonify(command_json(command))


def _accepted(command: Command) -> Response:
    response = jsonify(command_json(command))
    response.status_code = 202
    return response
