"""``POST /auth/login``, ``/auth/refresh``, ``/auth/logout`` and ``GET /auth/me``.

A wrong password, an unknown email and an inactive user get the same 401 body. An unknown
email still runs a dummy hash verify. Every attempt is audited, without the password.

Refresh and logout are public: the refresh cookie plus the X-CSRF-Token header authenticate
them (ADR 0018). Every refused refresh is one 401 ``session_invalid``; the audit row keeps the
reason. Audit entries never hold the token, the CSRF token or a cookie header.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from flask import Response, current_app, jsonify, request
from flask_smorest import Blueprint
from skyhaul_fleet_core.models import User
from sqlalchemy import select

from skyhaul_api.audit.service import record
from skyhaul_api.auth import sessions
from skyhaul_api.auth.cookies import (
    CSRF_HEADER,
    REFRESH_COOKIE,
    clear_session_cookies,
    set_session_cookies,
)
from skyhaul_api.auth.guard import principal
from skyhaul_api.auth.passwords import hash_password, needs_rehash, verify_dummy, verify_password
from skyhaul_api.auth.permissions import public, require
from skyhaul_api.auth.tokens import TOKEN_TTL, issue_for
from skyhaul_api.config import Config
from skyhaul_api.database import db
from skyhaul_api.errors import ApiError, error_response
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

COOKIE_HEADERS: Final = {
    **REQUEST_ID_HEADERS,
    "Set-Cookie": {"$ref": "#/components/headers/SetSessionCookies"},
}
SESSION_PARAMETERS: Final = [REQUEST_ID_PARAMETER, "CsrfHeader", "RefreshCookie"]


def _response_ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/responses/{name}"}


@blp.route("/auth/login", methods=["POST"])
@blp.doc(
    operationId="login",
    summary="Exchange email and password for an access token and a session cookie",
    description=(
        "Starts a new session family and sets the session cookies. A refresh cookie sent with "
        "the request is revoked first, so a planted cookie never survives a login. ADR 0018."
    ),
    security=[],
    parameters=[REQUEST_ID_PARAMETER, "RefreshCookie"],
    requestBody=json_body("LoginRequest"),
    responses=error_doc(400, 401, *BODY_ERRORS, 429),
)
@blp.response(200, schema_ref("LoginResponse"), description="OK", headers=COOKIE_HEADERS)
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
    now = datetime.now(UTC)
    presented = request.cookies.get(REFRESH_COOKIE)
    if presented:
        sessions.revoke_presented(session, presented, now)
    issued = sessions.start(session, user, now, _config())
    record(
        session,
        action="auth.login",
        outcome="success",
        actor=actor,
        target_type="user",
        target_id=target,
    )
    session.commit()
    return _session_response(issued, now)


@blp.route("/auth/refresh", methods=["POST"])
@blp.doc(
    operationId="refreshSession",
    summary="Rotate the refresh cookie and get a new access token",
    description=(
        "Public: no bearer, because the access token may have expired. Needs the refresh "
        "cookie and X-CSRF-Token. Every call rotates the cookie. A used token presented again "
        "after the grace window revokes the whole family. Rate limit 30/min per ip. ADR 0018."
    ),
    security=[],
    parameters=SESSION_PARAMETERS,
    responses={
        "401": _response_ref("SessionInvalid"),
        "403": _response_ref("CsrfFailed"),
        "409": _response_ref("RefreshSuperseded"),
        **error_doc(429),
    },
)
@blp.response(200, schema_ref("RefreshResponse"), description="OK", headers=COOKIE_HEADERS)
@public
def refresh_session() -> Response:
    session = db()
    now = datetime.now(UTC)
    token = request.cookies.get(REFRESH_COOKIE, "")
    csrf = request.headers.get(CSRF_HEADER, "")
    try:
        if not token:
            raise sessions.SessionInvalid("unknown")
        issued = sessions.rotate(session, token, csrf, now, _config())
    except sessions.SessionReused as reuse:
        record(
            session,
            action="auth.refresh.reuse",
            outcome="denied",
            actor=reuse.user_id,
            target_type="user",
            target_id=str(reuse.user_id),
            data={"family_id": str(reuse.family_id)},
        )
        session.commit()
        return _session_invalid()
    except sessions.SessionInvalid as refused:
        _audit_refresh_failure(session, refused.reason, refused.user_id)
        return _session_invalid()
    except sessions.CsrfFailed as failed:
        session.rollback()
        _audit_refresh_failure(session, "csrf", failed.user_id)
        raise _csrf_failed() from None
    except sessions.RefreshSuperseded:
        session.rollback()
        raise ApiError(
            409, "refresh_superseded", "The session was refreshed by another request; retry."
        ) from None
    session.commit()
    return _session_response(issued, now)


@blp.route("/auth/logout", methods=["POST"])
@blp.doc(
    operationId="logout",
    summary="End the session: revoke the refresh family and clear the cookies",
    description=(
        "Public: works with an expired access token. Idempotent: without a refresh cookie, or "
        "with an unknown, expired or revoked one, it clears the cookies and answers 204. A live "
        "cookie with a wrong X-CSRF-Token is 403 and revokes nothing. Rate limit 30/min per ip. "
        "ADR 0018."
    ),
    security=[],
    parameters=SESSION_PARAMETERS,
    responses={"403": _response_ref("CsrfFailed"), **error_doc(429)},
)
@blp.response(204, description="Session ended; both cookies cleared.", headers=COOKIE_HEADERS)
@public
def logout() -> Response:
    session = db()
    token = request.cookies.get(REFRESH_COOKIE, "")
    csrf = request.headers.get(CSRF_HEADER, "")
    user_id = None
    if token:
        try:
            user_id = sessions.revoke_family(session, token, csrf, datetime.now(UTC))
        except sessions.CsrfFailed:
            session.rollback()
            raise _csrf_failed() from None
    if user_id is not None:
        record(
            session,
            action="auth.logout",
            outcome="success",
            actor=user_id,
            target_type="user",
            target_id=str(user_id),
        )
    session.commit()
    response = Response(status=204)
    clear_session_cookies(response, secure=_config().auth_cookie_secure)
    return response


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


def _config() -> Config:
    config: Config = current_app.extensions["skyhaul.config"]
    return config


def _session_response(issued: sessions.Issued, now: datetime) -> Response:
    config = _config()
    body: dict[str, Any] = {
        "access_token": issue_for(sessions.access_claims(issued.user), config.jwt_secret, now),
        "token_type": "Bearer",
        "expires_in": int(TOKEN_TTL.total_seconds()),
        "user": user_json(issued.user),
        "csrf_token": issued.csrf,
    }
    response = jsonify(body)
    set_session_cookies(
        response,
        issued.token,
        issued.csrf,
        issued.expires_at,
        now,
        secure=config.auth_cookie_secure,
    )
    return response


def _audit_refresh_failure(session: Any, reason: str, user_id: Any) -> None:
    record(
        session,
        action="auth.refresh",
        outcome="failure",
        actor=user_id,
        target_type="user" if user_id else None,
        target_id=str(user_id) if user_id else None,
        data={"reason": reason},
    )
    session.commit()


def _session_invalid() -> Response:
    response = error_response(401, "session_invalid", "The session has ended; sign in again.")
    clear_session_cookies(response, secure=_config().auth_cookie_secure)
    return response


def _csrf_failed() -> ApiError:
    return ApiError(403, "csrf_failed", "The CSRF token is missing or wrong.")
