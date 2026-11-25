"""Redis latest state (``drone:{id}``, newer ts wins per field group) and ``drone.state``
events on the ``events`` channel, at most one per drone per flush."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final

import redis
from skyhaul_contracts.message import format_ts
from skyhaul_fleet_core.keys import EVENTS_CHANNEL, drone_state_key
from skyhaul_fleet_core.live_state import STATE_TTL_S, read_live_state

from skyhaul_ingestor.handlers import StateUpdate

# KEYS[1] the hash. ARGV: group, ts (wire form, fixed width, so string order is time order),
# seen_at, ttl, then field/value pairs (JSON-encoded). Returns the hash if anything changed.
NEWER_WINS: Final = """
local key, group, ts = KEYS[1], ARGV[1], ARGV[2]
local group_ts = redis.call('HGET', key, group .. '_ts')
if group_ts and group_ts >= ts then return false end
local args = {group .. '_ts', ts}
for i = 5, #ARGV do args[#args + 1] = ARGV[i] end
redis.call('HSET', key, unpack(args))
local seen = redis.call('HGET', key, 'ts_raw')
if not seen or seen < ts then
  redis.call('HSET', key, 'ts_raw', ts, 'ts', cjson.encode(ts), 'last_seen_at', ARGV[3],
    'link', '"UP"')
end
redis.call('EXPIRE', key, ARGV[4])
return redis.call('HGETALL', key)
"""


class StateWriter:
    def __init__(self, client: redis.Redis) -> None:
        self._client = client
        self._script = client.register_script(NEWER_WINS)

    def write(self, updates: Sequence[tuple[str, StateUpdate]], seen_at: datetime) -> int:
        """Apply the updates, then publish one ``drone.state`` per changed drone. Idempotent,
        so a retried flush is safe. Returns the number of events sent."""
        if not updates:
            return 0
        seen = json.dumps(format_ts(seen_at))
        pipe = self._client.pipeline(transaction=False)
        for drone_id, update in updates:
            self._script(keys=[drone_state_key(drone_id)], args=_args(update, seen), client=pipe)
        latest: dict[str, Any] = {}
        for (drone_id, _), result in zip(updates, pipe.execute(), strict=True):
            if result:
                latest[drone_id] = dict(zip(result[::2], result[1::2], strict=True))
        pipe = self._client.pipeline(transaction=False)
        for drone_id, raw in latest.items():
            event = {"drone_id": drone_id, "state": read_live_state(raw)}
            pipe.publish(EVENTS_CHANNEL, json.dumps({"event": "drone.state", "data": event}))
        pipe.execute()
        return len(latest)

    def publish(self, events: Sequence[dict[str, Any]]) -> None:
        """Publish ``{"event", "data"}`` items on the events channel, in order."""
        if not events:
            return
        pipe = self._client.pipeline(transaction=False)
        for event in events:
            pipe.publish(EVENTS_CHANNEL, json.dumps(event))
        pipe.execute()


def _args(update: StateUpdate, seen: str) -> list[str | int]:
    args: list[str | int] = [update.group, update.ts, seen, STATE_TTL_S]
    for name, value in update.fields.items():
        args += [name, json.dumps(value)]
    return args
