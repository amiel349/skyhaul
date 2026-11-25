"""The fan-out thread: Redis channel ``events`` to Socket.IO rooms (ADR 0014).

One thread per API process. Redis pub/sub is fire-and-forget: while Redis is down, pushes
are lost and clients refetch over REST on reconnect. The thread reconnects with a backoff.
Event data is passed through unchanged (alert text is data, never interpreted).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any, Final

import redis
from skyhaul_fleet_core.keys import EVENTS_CHANNEL

from skyhaul_api.realtime.rooms import route

log = logging.getLogger(__name__)

POLL_S: Final = 1.0
MAX_BACKOFF_S: Final = 10.0

type Emit = Callable[[str, dict[str, Any], tuple[str, ...]], None]


class EventFanout:
    def __init__(self, redis_url: str, emit: Emit) -> None:
        self._redis_url = redis_url
        self._emit = emit
        self._stop = threading.Event()
        self._subscribed = threading.Event()
        self._thread = threading.Thread(target=self._run, name="socket-fanout", daemon=True)

    @property
    def subscribed(self) -> bool:
        return self._subscribed.is_set()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(5)

    def handle(self, raw: bytes | str) -> bool:
        """Emit one Redis message to its rooms. False if it was dropped."""
        routed = route(raw)
        if routed is None:
            return False
        event, data, rooms = routed
        self._emit(event, data, rooms)
        return True

    def _run(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            client = redis.Redis.from_url(
                self._redis_url, socket_connect_timeout=2, health_check_interval=30
            )
            try:
                pubsub = client.pubsub(ignore_subscribe_messages=True)  # type: ignore[no-untyped-call]
                pubsub.subscribe(EVENTS_CHANNEL)
                self._subscribed.set()
                backoff = 0.5
                while not self._stop.is_set():
                    message = pubsub.get_message(timeout=POLL_S)
                    if message is not None and message["type"] == "message":
                        self.handle(message["data"])
            except (redis.RedisError, OSError) as exc:
                log.warning("event fan-out lost Redis (%s); retrying", type(exc).__name__)
            except Exception:  # one bad emit must not end the fan-out
                log.exception("event fan-out failed; resubscribing")
            finally:
                self._subscribed.clear()
                client.close()
            self._stop.wait(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_S)
