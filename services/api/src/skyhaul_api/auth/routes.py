"""``POST /auth/login`` and ``GET /auth/me``.

A wrong password, an unknown email and an inactive user get the same 401 body. An unknown
email still runs a dummy hash verify. Every attempt is audited, without the password.
"""

from __future__ import annotations

from flask import Response, current_app, jsonify
from flask_smorest import Blueprint
from skyhaul_fleet_core.models import User
from sqlalchemy import select

from skyhaul_api.audit.service import record
from skyhaul_api.auth.guard import principal
from skyhaul_api.auth.passwords import hash_password, needs_rehash, verify_dummy, verify_password
from skyhaul_api.auth.permissions import public, require
from skyhaul_api.auth.tokens import TOKEN_TTL, issue_token
from skyhaul_api.database import db
from skyhaul_api.errors import ApiError
from skyhaul_api.openapi import (
    BODY_ERRORS,
    REQUEST_ID_HEADERS,
    REQUEST_ID_PARAMETER,
    error_doc,
    json_body,
    schema_ref,
)
from skyhaul_api.users.schemas import LoginSchema, user_json
from skyhaul_api.validation import load_body

blp = Blueprint("auth", __name__)


@blp.route("/auth/login", methods=["POST"])
@blp.doc(
    operationId="login",
    summary="Exchange email and password for an access token",
    security=[],
    parameters=[REQUEST_ID_PARAMETER],
    requestBody=json_body("LoginRequest"),
    responses=error_doc(400, 401, *BODY_ERRORS, 429),
)
@blp.response(200, schema_ref("LoginResponse"), description="OK", headers=REQUEST_ID_HEADERS)
@public
def login() -> Response:
    body = load_body(LoginSchema())
    session = db()
    user = session.scalar(select(User).where(User.email == body["email"]))
    if user is None:
        verify_dummy(body["password"])
        reason = "unknown_email"
    elif not verify_password(user.password_hash, body["password"]):
        reason = "wrong_password"
    elif not user.is_active:
        reason = "inactive"
    else:
        reason = ""

    actor = user.id if user else None
    target = str(user.id) if user else None
    if user is None or reason:
        record(
            session,
            action="auth.login",
            outcome="failure",
            actor=actor,
            target_type="user",
            target_id=target,
            data={"reason": reason},
        )
        session.commit()
        raise ApiError(401, "invalid_credentials", "The email or password is wrong.")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body["password"])
    record(
        session,
        action="auth.login",
        outcome="success",
        actor=actor,
        target_type="user",
        target_id=target,
    )
    session.commit()
    secret: str = current_app.extensions["skyhaul.config"].jwt_secret
    return jsonify(
        access_token=issue_token(user.id, user.role.value, secret),
        token_type="Bearer",  # noqa: S106  # the scheme name, not a secret
        expires_in=int(TOKEN_TTL.total_seconds()),
        user=user_json(user),
    )


@blp.route("/auth/me", methods=["GET"])
@blp.doc(
    operationId="getMe",
    summary="The authenticated user",
    parameters=[REQUEST_ID_PARAMETER],
    responses=error_doc(401),
    **{"x-permission": "any"},
)
@blp.response(200, schema_ref("User"), description="OK", headers=REQUEST_ID_HEADERS)
@require()
def me() -> Response:
    return jsonify(user_json(principal().user))
