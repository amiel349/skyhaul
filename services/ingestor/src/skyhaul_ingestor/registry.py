"""Registered-drone cache: positive answers for 30 s, negative ones for 5 s.

Drone ids are never reused, so a stale entry can at worst keep storing a just-deleted
drone's telemetry for 30 s, never attribute it to another drone.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Final

POSITIVE_TTL_S: Final = 30.0
NEGATIVE_TTL_S: Final = 5.0


class RegistryCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[str, tuple[bool, float]] = {}

    def registered(
        self, drone_ids: Iterable[str], load: Callable[[set[str]], set[str]]
    ) -> set[str]:
        """The registered subset of ``drone_ids``. ``load`` is called once, for the misses."""
        now = self._clock()
        wanted = set(drone_ids)
        misses = {d for d in wanted if self._entries.get(d, (False, 0.0))[1] <= now}
        if misses:
            found = load(misses)
            for drone_id in misses:
                ttl = POSITIVE_TTL_S if drone_id in found else NEGATIVE_TTL_S
                self._entries[drone_id] = (drone_id in found, now + ttl)
        return {d for d in wanted if self._entries[d][0]}
