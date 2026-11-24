"""The simulated drone: plain state, changed only under the board lock.

A `Drone` lives on the `JobsBoard`. Handlers change a private copy through the board's
`World` (copy-on-write, committed when the transaction ends); scheduler ticks change the
board's own object while `due()` holds the lock. Nothing here does I/O.
"""

from __future__ import annotations

import hashlib
import random
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final

from skyhaul_contracts import AlertCode, FlightState, MissionState

if TYPE_CHECKING:
    from skyhaul_drone_sim.outbox import Outgoing

DEDUP_CACHE_SIZE: Final = 256
"""Replies kept per drone for duplicate `msg_id`s. A command older than the last 256 a drone
handled is treated as new: the dedup window is bounded, not exact."""

DEFAULT_CRUISE_MPS: Final = 10.0

AIRBORNE: Final = frozenset(
    {
        FlightState.TAKING_OFF,
        FlightState.HOVERING,
        FlightState.FLYING,
        FlightState.RETURNING,
        FlightState.LANDING,
    }
)


@dataclass(frozen=True, slots=True)
class GeoPoint:
    lat: float
    lon: float


@dataclass(frozen=True, slots=True)
class Target:
    """Where the drone is flying to. `altitude_m` is above home; 0 means touch down."""

    point: GeoPoint
    altitude_m: float
    speed_mps: float | None = None
    """The leg's own speed (GOTO `speed_mps`), or None for the drone's cruise speed."""


@dataclass(slots=True)
class Mission:
    mission_id: str
    waypoints: tuple[Target, ...]
    index: int = 0
    """The waypoint being flown to, which is also the number already reached."""
    state: MissionState = MissionState.ACTIVE

    @property
    def active(self) -> bool:
        return self.state is MissionState.ACTIVE


@dataclass(slots=True)
class Drone:
    drone_id: str
    home: GeoPoint
    spawned_at: float
    """Clock.monotonic() seconds when the drone was spawned."""
    rng: random.Random
    geofence_radius_m: float
    pos: GeoPoint
    altitude_m: float = 0.0
    heading_deg: float = 0.0
    ground_speed_mps: float = 0.0
    climbing: bool = False
    cruise_mps: float = DEFAULT_CRUISE_MPS
    target: Target | None = None
    state: FlightState = FlightState.LANDED
    battery_pct: float = 100.0
    mission: Mission | None = None
    alerts_latched: set[AlertCode] = field(default_factory=set)
    recent_cmds: OrderedDict[str, list[Outgoing]] = field(default_factory=OrderedDict)
    """Replies to the last DEDUP_CACHE_SIZE commands, by command msg_id."""

    @property
    def airborne(self) -> bool:
        return self.state in AIRBORNE

    @property
    def armed(self) -> bool:
        return self.state is not FlightState.LANDED

    @property
    def mission_active(self) -> bool:
        return self.mission is not None and self.mission.active

    def copy(self) -> Drone:
        """A private copy. Cached replies are immutable, so the cache is copied shallowly."""
        rng = random.Random()  # noqa: S311 - simulation, not security
        rng.setstate(self.rng.getstate())
        mission = None if self.mission is None else replace(self.mission)
        return replace(
            self,
            rng=rng,
            mission=mission,
            alerts_latched=set(self.alerts_latched),
            recent_cmds=OrderedDict(self.recent_cmds),
        )

    def remember(self, msg_id: str, replies: list[Outgoing]) -> None:
        """Cache the replies to a command, dropping the oldest beyond DEDUP_CACHE_SIZE."""
        self.recent_cmds[msg_id] = list(replies)
        while len(self.recent_cmds) > DEDUP_CACHE_SIZE:
            self.recent_cmds.popitem(last=False)


def seed_from(seed: int, drone_id: str) -> int:
    """A per-drone seed from a stable hash, so runs repeat whatever the spawn order."""
    digest = hashlib.blake2b(f"{seed}:{drone_id}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big")
