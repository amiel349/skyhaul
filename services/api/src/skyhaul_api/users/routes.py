"""``/users``: list, create, change role or active flag. Admin only (``user:admin``)."""

from __future__ import annotations

import uuid

from flask import Response, jsonify
from flask_smorest import Blueprint
from skyhaul_fleet_core.models import Role, User
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from skyhaul_api.audit.service import record
from skyhaul_api.auth.guard import principal
from skyhaul_api.auth.passwords import hash_password
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
from skyhaul_api.users.schemas import UserCreateSchema, UserUpdateSchema, user_json
from skyhaul_api.validation import load_body, page_args

blp = Blueprint("users", __name__)

USER_ADMIN = {"x-permission": Permission.USER_ADMIN.value}


@blp.route("/users", methods=["GET"])
@blp.doc(
    operationId="listUsers",
    summary="List users",
    parameters=[REQUEST_ID_PARAMETER, "Limit", "Cursor"],
    responses=error_doc(*PROTECTED_ERRORS),
    **USER_ADMIN,
)
@blp.response(200, schema_ref("UserPage"), description="OK", headers=REQUEST_ID_HEADERS)
@require(Permission.USER_ADMIN)
def list_users() -> Response:
    limit, cursor = page_args()
    page = paginate(
        db(),
        select(User),
        ts_column=User.created_at,
        id_column=User.id,
        id_type=uuid.UUID,
        limit=limit,
        cursor=cursor,
    )
    return jsonify(items=[user_json(u) for u in page.items], next_cursor=page.next_cursor)


@blp.route("/users", methods=["POST"])
@blp.doc(
    operationId="createUser",
    summary="Create a user",
    parameters=[REQUEST_ID_PARAMETER],
    requestBody=json_body("UserCreate"),
    responses=error_doc(*PROTECTED_ERRORS, *BODY_ERRORS, 409),
    **USER_ADMIN,
)
@blp.response(201, schema_ref("User"), description="Created", headers=REQUEST_ID_HEADERS)
@require(Permission.USER_ADMIN)
def create_user() -> Response:
    body = load_body(UserCreateSchema())
    session = db()
    if session.scalar(select(User.id).where(User.email == body["email"])) is not None:
        raise _email_taken()
    user = User(
        email=body["email"], password_hash=hash_password(body["password"]), role=Role(body["role"])
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError:
        raise _email_taken() from None
    record(
        session,
        action="user.create",
        outcome="success",
        actor=principal().user.id,
        target_type="user",
        target_id=str(user.id),
        data={"role": user.role.value},
    )
    session.commit()
    response = jsonify(user_json(user))
    response.status_code = 201
    return response


@blp.route("/users/<uuid:user_id>", methods=["PATCH"])
@blp.doc(
    operationId="updateUser",
    summary="Change a user's role or active flag",
    parameters=[REQUEST_ID_PARAMETER],
    requestBody=json_body("UserUpdate"),
    responses=error_doc(*PROTECTED_ERRORS, *BODY_ERRORS, 404),
    **USER_ADMIN,
)
@blp.response(200, schema_ref("User"), description="OK", headers=REQUEST_ID_HEADERS)
@require(Permission.USER_ADMIN)
def update_user(user_id: uuid.UUID) -> Response:
    session = db()
    user = session.get(User, user_id)
    if user is None:
        raise ApiError(404, "not_found", "The user does not exist.")
    body = load_body(UserUpdateSchema())
    if "role" in body:
        user.role = Role(body["role"])
    if "is_active" in body:
        user.is_active = body["is_active"]
    record(
        session,
        action="user.update",
        outcome="success",
        actor=principal().user.id,
        target_type="user",
        target_id=str(user.id),
        data={"changes": body},
    )
    session.commit()
    return jsonify(user_json(user))


def _email_taken() -> ApiError:
    return ApiError(409, "email_taken", "A user with this email exists.")
