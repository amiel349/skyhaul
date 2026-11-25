"""The ``before_request`` guard: default deny, authentication and the permission check.

A route without ``@require`` or ``@public`` is refused. A protected route needs a valid
bearer token whose user exists and is active; the role is read from the database row, so
a role change or deactivation applies to the very next request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from flask import Flask, current_app, g, request
from skyhaul_fleet_core.models import Role, User

from skyhaul_api.audit.service import record_now
from skyhaul_api.auth.permissions import ANY_USER, PUBLIC, Permission, access_of, has_permission
from skyhaul_api.auth.tokens import InvalidTokenError, verify_token
from skyhaul_api.database import db
from skyhaul_api.errors import ApiError

log = logging.getLogger(__name__)

BEARER = "Bearer "


@dataclass(frozen=True)
class Principal:
    user: User

    @property
    def role(self) -> Role:
        return self.user.role


def principal() -> Principal:
    current: Principal = g.principal
    return current


def init_guard(app: Flask) -> None:
    app.before_request(_guard)


def _guard() -> None:
    if request.endpoint is None:
        return  # no route matched: the 404 or 405 follows
    access = access_of(current_app.view_functions.get(request.endpoint))
    if access is None:
        log.error("route %s has neither @require nor @public; refused", request.endpoint)
        raise ApiError(403, "forbidden", "The route has no access rule.")
    if access == PUBLIC:
        return
    user = _authenticate()
    g.principal = Principal(user)
    if access != ANY_USER and not has_permission(user.role, Permission(access)):
        _audit_denied(user, access)
        raise ApiError(403, "forbidden", "The role lacks the permission.")


def _authenticate() -> User:
    header = request.headers.get("Authorization", "")
    if not header.startswith(BEARER):
        raise _unauthorized()
    try:
        user_id = verify_token(header.removeprefix(BEARER).strip(), _secret())
    except InvalidTokenError:
        raise _unauthorized() from None
    user = db().get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized()
    return user


def _audit_denied(user: User, permission: str) -> None:
    rule = request.url_rule.rule if request.url_rule else request.path
    record_now(
        action="permission.denied",
        outcome="denied",
        actor=user.id,
        data={"route": f"{request.method} {rule}", "permission": permission},
    )


def _secret() -> str:
    secret: str = current_app.extensions["skyhaul.config"].jwt_secret
    return secret


def _unauthorized() -> ApiError:
    return ApiError(401, "unauthorized", "Authentication is required.")
