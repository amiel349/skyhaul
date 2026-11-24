"""`skyhaul-mcp-sim`: the stdio MCP server. stdout is the protocol, so logs go to stderr."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, Final

from skyhaul_mcp_sim.config import Config
from skyhaul_mcp_sim.server import build_server

_STANDARD: Final = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Fields passed with `extra=` become top-level keys;
    callers never pass message bodies, credentials or headers."""

    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        doc.update({k: v for k, v in vars(record).items() if k not in _STANDARD})
        doc.setdefault("correlation_id", None)
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    # pika logs connection parameters at INFO; httpx logs every request URL.
    for name in ("pika", "httpx", "httpcore", "mcp"):
        logging.getLogger(name).setLevel(logging.WARNING)


def run() -> None:
    configure_logging()
    build_server(Config.from_env()).run(transport="stdio")


if __name__ == "__main__":
    run()
