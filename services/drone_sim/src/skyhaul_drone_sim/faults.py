"""Injected faults (ADR 0008, design note section 5): state on one drone, changed only under
the board lock and expired on scheduler ticks.

- LINK_LOSS: commands are dropped (acked, not cached, not applied), nothing is published,
  delayed replies falling due are dropped, physics keeps running. When it expires, one ALERT
  LINK_RESTORED. Clearing the faults or resetting the namespace sends no alert.
- NACK_NEXT: the next `count` new commands get NACK INJECTED and are not applied.
- REPLY_DELAY: replies become scheduled one-shots on the board, never a sleep.
- BATTERY_DRAIN: the drain rate is multiplied by `factor`.
- GPS_DRIFT: the reported position is offset by up to `drift_m`, drawn from the drone's own
  seeded RNG on every tick. Physics and arrival use the true position.

Everything here is in memory only, so it is safe under the lock.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from skyhaul_contracts import AlertCode, AlertSeverity, MessageType
from skyhaul_contracts.v1 import AlertPayload

from skyhaul_drone_sim.outbox import Outgoing, new_message

if TYPE_CHECKING:
    from skyhaul_drone_sim.model import Drone


class FaultKind(StrEnum):
    LINK_LOSS = "LINK_LOSS"
    NACK_NEXT = "NACK_NEXT"
    REPLY_DELAY = "REPLY_DELAY"
    BATTERY_DRAIN = "BATTERY_DRAIN"
    GPS_DRIFT = "GPS_DRIFT"


@dataclass(frozen=True, slots=True)
class Fault:
    """One active fault. Only the fields of its kind are set; `expires_at` is
    Clock.monotonic() seconds, or None for a fault that lasts until cleared (or, for
    NACK_NEXT, until its count is used up)."""

    kind: FaultKind
    expires_at: float | None = None
    count: int | None = None
    delay_s: float | None = None
    factor: float | None = None
    drift_m: float | None = None


def apply_fault(d: Drone, fault: Fault) -> None:
    """Add `fault`, replacing an active fault of the same kind (no LINK_RESTORED then)."""
    d.faults[fault.kind] = fault


def clear_faults(d: Drone) -> None:
    """Remove every fault at once. Ending a link loss this way sends no LINK_RESTORED."""
    d.faults.clear()
    d.gps_offset_m = (0.0, 0.0)


def tick_faults(d: Drone, now: float) -> list[Outgoing]:
    """Once per scheduler tick: expire faults, then redraw the GPS drift offset. Returns the
    LINK_RESTORED alert when a link loss ended on this tick."""
    events: list[Outgoing] = []
    for kind, fault in list(d.faults.items()):
        if fault.expires_at is not None and now >= fault.expires_at:
            del d.faults[kind]
            if kind is FaultKind.LINK_LOSS:
                events.append(_link_restored(d))
    drift = d.faults.get(FaultKind.GPS_DRIFT)
    d.gps_offset_m = (0.0, 0.0) if drift is None else _random_offset(d, drift.drift_m or 0.0)
    return events


def link_lost(d: Drone) -> bool:
    return FaultKind.LINK_LOSS in d.faults


def take_injected_nack(d: Drone) -> bool:
    """True if this command must get NACK INJECTED; uses up one NACK_NEXT count."""
    fault = d.faults.get(FaultKind.NACK_NEXT)
    if fault is None or not fault.count:
        return False
    if fault.count <= 1:
        del d.faults[FaultKind.NACK_NEXT]
    else:
        d.faults[FaultKind.NACK_NEXT] = replace(fault, count=fault.count - 1)
    return True


def reply_delay_s(d: Drone) -> float | None:
    fault = d.faults.get(FaultKind.REPLY_DELAY)
    return None if fault is None else fault.delay_s


def drain_factor(d: Drone) -> float:
    fault = d.faults.get(FaultKind.BATTERY_DRAIN)
    return 1.0 if fault is None or fault.factor is None else fault.factor


def fault_view(fault: Fault, now: float) -> dict[str, Any]:
    """What the control API shows for one fault: its kind, its parameters, and the seconds
    left (null when it has no duration)."""
    view: dict[str, Any] = {"kind": fault.kind.value}
    for name in ("count", "delay_s", "factor", "drift_m"):
        value = getattr(fault, name)
        if value is not None:
            view[name] = value
    expires_in = None if fault.expires_at is None else round(max(fault.expires_at - now, 0.0), 3)
    view["expires_in_s"] = expires_in
    return view


def _random_offset(d: Drone, drift_m: float) -> tuple[float, float]:
    """(north, east) meters, uniform over the disk of radius `drift_m`."""
    radius = drift_m * math.sqrt(d.rng.random())
    angle = d.rng.uniform(0.0, 2.0 * math.pi)
    return radius * math.cos(angle), radius * math.sin(angle)


def _link_restored(d: Drone) -> Outgoing:
    payload = AlertPayload(
        code=AlertCode.LINK_RESTORED,
        severity=AlertSeverity.INFO,
        message="link restored",
        value=None,
    )
    return Outgoing(new_message(MessageType.ALERT, d.drone_id, payload, None), persistent=True)
