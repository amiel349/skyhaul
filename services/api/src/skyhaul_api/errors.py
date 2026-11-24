"""One error shape for every non-2xx answer: ``{error, detail, fields?, request_id}``.

``detail`` is fixed text chosen by the server. It never echoes headers, tokens or input
values, and a server fault never shows a stack trace (that goes to the log only).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import (
    HTTPException,
    MethodNotAllowed,
    RequestEntityTooLarge,
    UnsupportedMediaType,
)

from skyhaul_api.config import MAX_BODY_BYTES
from skyhaul_api.logs import request_id

log = logging.getLogger(__name__)

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})

# Status -> (error code, detail) for errors raised by Flask, werkzeug or webargs.
_HTTP_ERRORS: Mapping[int, tuple[str, str]] = {
    400: ("bad_request", "The request is malformed."),
    401: ("unauthorized", "Authentication is required."),
    403: ("forbidden", "The role lacks the permission."),
    404: ("not_found", "The resource does not exist."),
    405: ("method_not_allowed", "The method is not allowed on this resource."),
    409: ("conflict", "The request conflicts with the current state."),
    413: ("payload_too_large", f"The body is larger than {MAX_BODY_BYTES // 1024} KiB."),
    415: ("unsupported_media_type", "The body must be application/json."),
    422: ("validation_failed", "The request failed validation."),
    429: ("rate_limited", "Too many requests."),
    500: ("internal_error", "The server failed to handle the request."),
    503: ("unavailable", "A dependency is unavailable."),
}


class ApiError(Exception):
    """Raise from a view to answer with the error shape. ``detail`` must not hold input."""

    def __init__(
        self,
        status: int,
        error: str,
        detail: str,
        fields: Mapping[str, list[str]] | None = None,
    ) -> None:
        super().__init__(error)
        self.status = status
        self.error = error
        self.detail = detail
        self.fields = fields


def error_response(
    status: int, error: str, detail: str, fields: Mapping[str, list[str]] | None = None
) -> Response:
    body: dict[str, Any] = {"error": error, "detail": detail, "request_id": request_id()}
    if fields:
        body["fields"] = dict(fields)
    response = jsonify(body)
    response.status_code = status
    return response


def init_errors(app: Flask) -> None:
    """Register the body checks and the error handlers. Call after ``Api.init_app`` so these
    handlers replace flask-smorest's."""
    app.before_request(_check_body)
    app.register_error_handler(ApiError, _handle_api_error)
    app.register_error_handler(HTTPException, _handle_http_exception)
    app.register_error_handler(Exception, _handle_unexpected)


def _check_body() -> None:
    """Refuse oversized and non-JSON bodies before routing, on every endpoint."""
    if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
        raise RequestEntityTooLarge()
    has_body = bool(request.content_length) or "chunked" in request.headers.get(
        "Transfer-Encoding", ""
    )
    if request.method in _BODY_METHODS and has_body and not request.is_json:
        raise UnsupportedMediaType()


def _handle_api_error(exc: ApiError) -> Response:
    return error_response(exc.status, exc.error, exc.detail, exc.fields)


def _handle_http_exception(exc: HTTPException) -> Response:
    status = exc.code or 500
    error, detail = _HTTP_ERRORS.get(status, (f"http_{status}", "The request failed."))
    response = error_response(status, error, detail, _validation_fields(exc))
    if isinstance(exc, MethodNotAllowed) and exc.valid_methods:
        response.headers["Allow"] = ", ".join(sorted(exc.valid_methods))
    return response


def _handle_unexpected(exc: Exception) -> Response:
    log.exception("unhandled error")
    error, detail = _HTTP_ERRORS[500]
    return error_response(500, error, detail)


def _validation_fields(exc: HTTPException) -> dict[str, list[str]] | None:
    """Field messages from a webargs 422 (``exc.data["messages"]``), keyed by dotted path
    without the location (``json``, ``query``). Marshmallow messages hold no input values."""
    data = getattr(exc, "data", None)
    messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, dict):
        return None
    fields: dict[str, list[str]] = {}
    for location_messages in messages.values():
        _flatten(location_messages, "", fields)
    return fields or None


def _flatten(node: Any, prefix: str, out: dict[str, list[str]]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            _flatten(value, f"{prefix}.{key}" if prefix else str(key), out)
    elif isinstance(node, list):
        out.setdefault(prefix or "_body", []).extend(str(item) for item in node)
    else:
        out.setdefault(prefix or "_body", []).append(str(node))
