"""JSON log lines on stderr. Fields passed with `extra=` (correlation_id, drone_id, ...)
become top-level keys. Callers never pass message bodies, credentials or headers."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, Final

_STANDARD: Final = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "thread": record.threadName,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD:
                doc[key] = value
        doc.setdefault("correlation_id", None)
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, default=str)


def configure(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # pika logs every connection state change at INFO, with connection parameters.
    logging.getLogger("pika").setLevel(logging.WARNING)
