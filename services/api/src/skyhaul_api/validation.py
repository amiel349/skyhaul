"""Request input: JSON bodies through marshmallow schemas, page and time query parameters.

Errors carry marshmallow's field messages, which never contain the input values.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Final

from flask import request
from marshmallow import Schema, ValidationError

from skyhaul_api.errors import ApiError, flatten_messages
from skyhaul_api.pagination import DEFAULT_LIMIT, MAX_LIMIT

IDEMPOTENCY_KEY_RE: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")


def load_body(schema: Schema) -> dict[str, Any]:
    data = request.get_json(silent=True)
    if data is None:
        raise ApiError(400, "bad_request", "The body is not valid JSON.")
    try:
        loaded: dict[str, Any] = schema.load(data)
    except ValidationError as exc:
        raise ApiError(
            422,
            "validation_failed",
            "The request failed validation.",
            flatten_messages(exc.messages),
        ) from None
    return loaded


def page_args() -> tuple[int, str | None]:
    """``(limit, cursor)`` from the query string."""
    raw = request.args.get("limit")
    limit = DEFAULT_LIMIT
    if raw is not None:
        if not raw.isdigit() or not 1 <= int(raw) <= MAX_LIMIT:
            raise ApiError(400, "bad_request", f"limit must be an integer from 1 to {MAX_LIMIT}.")
        limit = int(raw)
    return limit, request.args.get("cursor")


def time_arg(name: str) -> datetime | None:
    raw = request.args.get(name)
    if raw is None:
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        raise ApiError(400, "bad_request", f"{name} must be an RFC 3339 date-time.") from None
    if value.tzinfo is None:
        raise ApiError(400, "bad_request", f"{name} must include a time zone.")
    return value


def idempotency_key() -> str | None:
    """The ``Idempotency-Key`` header (scoped to the user by the caller), if any."""
    key = request.headers.get("Idempotency-Key")
    if key is None:
        return None
    if not IDEMPOTENCY_KEY_RE.fullmatch(key):
        raise ApiError(400, "bad_request", "Idempotency-Key must be 1 to 64 of A-Z a-z 0-9 _ -.")
    return key


def bool_arg(name: str) -> bool | None:
    raw = request.args.get(name)
    if raw is None:
        return None
    if raw not in ("true", "false"):
        raise ApiError(400, "bad_request", f"{name} must be true or false.")
    return raw == "true"
