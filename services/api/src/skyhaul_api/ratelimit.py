"""Login rate limits with Flask-Limiter on Redis (ADR 0011): 5/min per (ip, email) and
20/min per ip. With Redis down the limiter fails closed: the login answers 503.

Keys carry a hash of the email, never the email itself.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Final

from flask import Flask, Response, request
from flask_limiter import Limiter, RequestLimit
from limits.errors import StorageError
from redis import RedisError

from skyhaul_api.audit.service import record_now
from skyhaul_api.errors import error_response

log = logging.getLogger(__name__)

LOGIN_ENDPOINT: Final = "auth.login"
LOGIN_PER_IP_EMAIL: Final = "5/minute"
LOGIN_PER_IP: Final = "20/minute"
STORAGE_TIMEOUT_S: Final = 2


def client_ip() -> str:
    return request.remote_addr or "unknown"


def client_ip_and_email() -> str:
    body = request.get_json(silent=True)
    email = body.get("email") if isinstance(body, dict) else None
    digest = hashlib.sha256(str(email).strip().lower().encode()).hexdigest()[:32]
    return f"{client_ip()}|{digest}"


def _audit_breach(_limit: RequestLimit) -> None:
    record_now(action="auth.login", outcome="denied", actor=None, data={"reason": "rate_limited"})


def init_rate_limits(app: Flask, redis_url: str) -> Limiter:
    limiter = Limiter(
        client_ip,
        app=app,
        storage_uri=redis_url,
        storage_options={
            "socket_timeout": STORAGE_TIMEOUT_S,
            "socket_connect_timeout": STORAGE_TIMEOUT_S,
        },
        headers_enabled=True,
        swallow_errors=False,
        in_memory_fallback_enabled=False,
        key_prefix="skyhaul:ratelimit",
    )
    # The limiter checks a decorated view in its wrapper, so the wrapper replaces the view.
    login: Any = app.view_functions[LOGIN_ENDPOINT]
    login = limiter.limit(LOGIN_PER_IP, key_func=client_ip, on_breach=_audit_breach)(login)
    login = limiter.limit(
        LOGIN_PER_IP_EMAIL, key_func=client_ip_and_email, on_breach=_audit_breach
    )(login)
    app.view_functions[LOGIN_ENDPOINT] = login
    app.register_error_handler(StorageError, _storage_down)
    app.register_error_handler(RedisError, _storage_down)
    return limiter


def _storage_down(exc: Exception) -> Response:
    log.warning("rate limit storage unavailable (%s); refusing", type(exc).__name__)
    return error_response(503, "rate_limit_unavailable", "Rate limiting is unavailable.")
