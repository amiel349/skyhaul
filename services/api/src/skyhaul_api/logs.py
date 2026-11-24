"""JSON logs on stdout, one object per line, with the request id.

Never log header values, bodies, tokens or passwords: the access line has method, path,
status and duration only.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from datetime import UTC, datetime
from typing import Any, TextIO

from flask import Flask, Response, g, has_request_context, request

REQUEST_ID_HEADER = "X-Request-ID"
# Fields that callers may pass through ``extra=``; everything else on a record is ignored.
EXTRA_FIELDS = ("request_id", "correlation_id", "method", "path", "status", "duration_ms")

access_log = logging.getLogger("skyhaul_api.access")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for name in EXTRA_FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                entry[name] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


class RequestIdFilter(logging.Filter):
    """Adds the current request id to records logged during a request."""

    def filter(self, record: logging.LogRecord) -> bool:
        if has_request_context() and getattr(record, "request_id", None) is None:
            record.request_id = record.correlation_id = request_id()
        return True


class _JsonHandler(logging.StreamHandler[TextIO]):
    """Marks the handler this module installed, so configuring again replaces only it."""


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON handler on the root logger, replacing an earlier one (idempotent)."""
    handler = _JsonHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestIdFilter())
    root = logging.getLogger()
    for old in [h for h in root.handlers if isinstance(h, _JsonHandler)]:
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(level)


def request_id() -> str:
    """The id of the current request: the client's ``X-Request-ID`` if it is a UUID, else new."""
    current: str | None = g.get("request_id")
    if current is None:
        current = _accepted_request_id(request.headers.get(REQUEST_ID_HEADER))
        g.request_id = current
    return current


def _accepted_request_id(raw: str | None) -> str:
    if raw and len(raw) <= 64:
        try:
            return str(uuid.UUID(raw))
        except ValueError:
            pass
    return str(uuid.uuid4())


def init_request_logging(app: Flask) -> None:
    @app.before_request
    def start_request() -> None:
        g.started = time.perf_counter()
        request_id()

    @app.after_request
    def finish_request(response: Response) -> Response:
        rid = request_id()
        response.headers[REQUEST_ID_HEADER] = rid
        started: float = g.get("started", time.perf_counter())
        access_log.info(
            "request",
            extra={
                "request_id": rid,
                "correlation_id": rid,
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response
