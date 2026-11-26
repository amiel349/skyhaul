"""The two session cookies (ADR 0018). ``skyhaul_refresh`` is HttpOnly and only sent to
``/api/v1/auth``; ``skyhaul_csrf`` is readable by the SPA. Both SameSite=Strict, Secure unless
``AUTH_COOKIE_SECURE=false``."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from flask import Response

REFRESH_COOKIE: Final = "skyhaul_refresh"
CSRF_COOKIE: Final = "skyhaul_csrf"
CSRF_HEADER: Final = "X-CSRF-Token"
REFRESH_PATH: Final = "/api/v1/auth"


def cookie_settings(
    token: str, csrf: str, max_age: int, *, secure: bool
) -> list[tuple[str, str, dict[str, Any]]]:
    """Name, value and ``set_cookie`` options of each cookie."""
    common: dict[str, Any] = {"max_age": max_age, "secure": secure, "samesite": "Strict"}
    return [
        (REFRESH_COOKIE, token, {**common, "httponly": True, "path": REFRESH_PATH}),
        (CSRF_COOKIE, csrf, {**common, "httponly": False, "path": "/"}),
    ]


def set_session_cookies(
    response: Response, token: str, csrf: str, expires_at: datetime, now: datetime, *, secure: bool
) -> None:
    max_age = max(int((expires_at - now).total_seconds()), 0)
    for name, value, options in cookie_settings(token, csrf, max_age, secure=secure):
        response.set_cookie(name, value, **options)


def clear_session_cookies(response: Response, *, secure: bool) -> None:
    for name, _, options in cookie_settings("", "", 0, secure=secure):
        response.set_cookie(name, "", **options)
