"""Best-effort pushes of API-side changes (``mission.updated``, ``alert.updated``) on the
Redis ``events`` channel. The row is committed first and is the source of truth, so a Redis
failure only loses the push and is logged."""

from __future__ import annotations

import json
import logging
from typing import Any

import redis
from flask import current_app
from skyhaul_fleet_core.keys import EVENTS_CHANNEL

log = logging.getLogger(__name__)


def push(event: str, data: dict[str, Any]) -> None:
    client: redis.Redis = current_app.extensions["skyhaul.redis"]
    try:
        client.publish(EVENTS_CHANNEL, json.dumps({"event": event, "data": data}))
    except (redis.RedisError, OSError) as exc:
        log.warning("event not pushed (%s)", type(exc).__name__, extra={"event": event})
