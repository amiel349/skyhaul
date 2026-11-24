"""A drone on a board, a FakeClock and the real handlers: send commands and run scheduler
ticks with an explicit `dt`, no sleeps. Every message produced is checked against the
contracts (encoded and decoded again by the Codec)."""

from __future__ import annotations

import math
import uuid
from typing import Any

from pydantic import BaseModel
from skyhaul_contracts import Codec, CommandType, FlightState, Message, MessageType
from skyhaul_contracts.v1 import GotoPayload, StartMissionPayload, TakeoffPayload, Waypoint
from skyhaul_drone_sim.clock import FakeClock
from skyhaul_drone_sim.handlers import registry
from skyhaul_drone_sim.model import Drone, GeoPoint
from skyhaul_drone_sim.outbox import Outgoing
from skyhaul_drone_sim.physics import M_PER_DEG
from skyhaul_drone_sim.registry import DispatchResult

from sim_support.drones import HOME, spawned_board
from sim_support.messages import DRONE, command

CODEC = Codec()
TICK_S = 0.1


def check_valid(out: Outgoing) -> Message[Any]:
    """The message as a consumer would read it off the wire. Raises if it breaks a schema."""
    return CODEC.decode(CODEC.encode(out.message))


def offset_point(north_m: float = 0.0, east_m: float = 0.0, origin: GeoPoint = HOME) -> GeoPoint:
    return GeoPoint(
        origin.lat + north_m / M_PER_DEG,
        origin.lon + east_m / (M_PER_DEG * math.cos(math.radians(origin.lat))),
    )


def goto(point: GeoPoint, altitude_m: float = 30.0, speed_mps: float | None = None) -> GotoPayload:
    if speed_mps is None:
        return GotoPayload(lat=point.lat, lon=point.lon, altitude_m=altitude_m)
    return GotoPayload(lat=point.lat, lon=point.lon, altitude_m=altitude_m, speed_mps=speed_mps)


def mission(*points: GeoPoint, altitude_m: float = 30.0, mission_id: str = "m1") -> BaseModel:
    waypoints = [Waypoint(lat=p.lat, lon=p.lon, altitude_m=altitude_m) for p in points]
    return StartMissionPayload(mission_id=mission_id, waypoints=waypoints)


def of_type(messages: list[Outgoing], type: MessageType | str) -> list[Outgoing]:
    return [out for out in messages if out.message.type == type]


def reply_type(result: DispatchResult) -> MessageType:
    (reply,) = [r for r in result.replies if r.message.type in (MessageType.ACK, MessageType.NACK)]
    return reply.message.type


def nack_reason(result: DispatchResult) -> str:
    (reply,) = result.replies
    assert reply.message.type is MessageType.NACK
    return str(reply.message.payload.reason)


class Flight:
    def __init__(self, *, battery_pct: float = 100.0, drone_id: str = DRONE) -> None:
        self.clock = FakeClock()
        self.board = spawned_board(drone_id, battery_pct=battery_pct)
        self.drone_id = drone_id
        self.sent: list[Outgoing] = []

    @property
    def now(self) -> float:
        return self.clock.monotonic()

    @property
    def drone(self) -> Drone:
        drone = self.board.drone(self.drone_id)
        assert drone is not None
        return drone

    def set(self, **changes: Any) -> None:
        """Change drone fields directly, as if time had passed (low battery, position)."""
        with self.board.transaction() as world:
            drone = world.drone(self.drone_id)
            assert drone is not None
            for name, value in changes.items():
                setattr(drone, name, value)

    def send(
        self,
        type: CommandType | str,
        payload: BaseModel | None = None,
        *,
        correlation_id: str | None = None,
        msg: Message[Any] | None = None,
    ) -> DispatchResult:
        if msg is None:
            corr = correlation_id or str(uuid.uuid4())
            msg = command(type, self.drone_id, payload=payload, correlation_id=corr)
        with self.board.transaction() as world:
            result = registry.dispatch(world, msg, self.now)
        assert result.error is None, result.error
        for out in result.replies:
            check_valid(out)
        self.sent += result.replies
        return result

    def run(self, seconds: float, dt: float = TICK_S) -> list[Outgoing]:
        """Tick the board every `dt` for `seconds`. Returns what it emitted, all valid."""
        produced: list[Outgoing] = []
        for _ in range(round(seconds / dt)):
            self.clock.advance(dt)
            result = self.board.due(self.now, dt)
            assert result.errors == [], result.errors
            produced += result.messages
        for out in produced:
            check_valid(out)
        self.sent += produced
        return produced

    def run_until(self, state: FlightState, limit_s: float = 600.0) -> list[Outgoing]:
        produced: list[Outgoing] = []
        while self.drone.state is not state:
            assert self.now < limit_s, f"never reached {state}"
            produced += self.run(TICK_S)
        return produced

    def hovering(self, altitude_m: float = 30.0) -> None:
        """ARM, TAKEOFF and climb until HOVERING."""
        assert reply_type(self.send("ARM")) is MessageType.ACK
        takeoff = self.send("TAKEOFF", TakeoffPayload(altitude_m=altitude_m))
        assert reply_type(takeoff) is MessageType.ACK
        self.run_until(FlightState.HOVERING)
