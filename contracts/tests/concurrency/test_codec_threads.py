"""One Codec shared by many threads (issue #6, ADR 0003: the codec is behind a lock)."""

from __future__ import annotations

import threading
from typing import Any

import pytest
from pydantic import BaseModel
from skyhaul_contracts import (
    AlertCode,
    AlertSeverity,
    Codec,
    DecodeError,
    FlightState,
    Message,
    MessageType,
    NackReason,
)
from skyhaul_contracts.v1 import (
    AlertPayload,
    GotoPayload,
    HeartbeatPayload,
    NackPayload,
    PositionPayload,
    StartMissionPayload,
    Waypoint,
)

THREADS = 16
ROUND_TRIPS = 1_000


def _messages(codec: Codec, worker: int) -> list[Message[BaseModel]]:
    """A different mix of types per thread, so threads never share an input object."""
    drone = f"gw{worker}-d001"
    corr = f"00000000-0000-4000-8000-{worker:012d}"
    payloads: list[tuple[MessageType, BaseModel, str | None]] = [
        (MessageType.GOTO, GotoPayload(lat=52 + worker / 100, lon=21, altitude_m=40), corr),
        (
            MessageType.START_MISSION,
            StartMissionPayload(
                mission_id=f"m{worker}",
                waypoints=[Waypoint(lat=52, lon=21 + i / 100, altitude_m=50) for i in range(20)],
            ),
            corr,
        ),
        (
            MessageType.POSITION,
            PositionPayload(
                lat=-33.8, lon=151.2, altitude_m=99.5, heading_deg=worker, ground_speed_mps=7.25,
                target=None,
            ),
            corr,
        ),
        (
            MessageType.HEARTBEAT,
            HeartbeatPayload(flight_state=FlightState.FLYING, armed=True, uptime_s=worker),
            None,
        ),
        (
            MessageType.NACK,
            NackPayload(
                command_msg_id=corr, command_type="GOTO", reason=NackReason.OUT_OF_BOUNDS,
                detail=f"worker {worker}",
            ),
            corr,
        ),
        (
            MessageType.ALERT,
            AlertPayload(
                code=AlertCode.LOW_BATTERY, severity=AlertSeverity.WARNING, message="low",
                value=19.5,
            ),
            None,
        ),
    ]  # fmt: skip
    return [codec.new(t, drone, p, c) for t, p, c in payloads]


def test_16_threads_x_1000_round_trips_on_one_codec_all_equal_and_none_raise() -> None:
    codec = Codec()
    start = threading.Barrier(THREADS)
    mismatches: list[tuple[int, int]] = []
    raised: list[BaseException] = []
    lock = threading.Lock()

    def worker(n: int) -> None:
        inputs = _messages(codec, n)
        try:
            start.wait(timeout=10)
            for i in range(ROUND_TRIPS):
                msg = inputs[i % len(inputs)]
                if codec.decode(codec.encode(msg)) != msg:
                    with lock:
                        mismatches.append((n, i))
        except BaseException as exc:  # recorded and asserted below
            with lock:
                raised.append(exc)

    threads = [
        threading.Thread(target=worker, args=(n,), name=f"codec-{n}") for n in range(THREADS)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not any(t.is_alive() for t in threads), "a worker hung"
    assert raised == []
    assert mismatches == []
    # Exact counters are consistent with locking but do not prove it: under the GIL a lost
    # update is rare even without a lock. test_encode_decode_and_stats_hold_the_codec_lock
    # is the proof that the lock is held.
    stats = codec.stats()
    expected: dict[str, Any] = {"decoded": THREADS * ROUND_TRIPS, "encoded": THREADS * ROUND_TRIPS}
    assert {"decoded": stats.decoded, "encoded": stats.encoded} == expected
    assert stats.encode_errors == 0
    assert stats.rejected_by_reason == {}


class SpyLock:
    """A lock that remembers which thread holds it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner: int | None = None
        self.acquisitions = 0

    def __enter__(self) -> SpyLock:
        self._lock.acquire()
        self._owner = threading.get_ident()
        self.acquisitions += 1
        return self

    def __exit__(self, *exc: object) -> None:
        self._owner = None
        self._lock.release()

    def held_by_caller(self) -> bool:
        return self._owner == threading.get_ident()


def test_encode_decode_and_stats_hold_the_codec_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """White-box: swap in a spy lock and check the work runs inside it."""
    codec = Codec()
    spy = SpyLock()
    monkeypatch.setattr(codec, "_lock", spy)
    held: list[tuple[str, bool]] = []
    real_decode, real_encode = codec._decode, codec._encode

    def decode(body: bytes) -> Message[BaseModel]:
        held.append(("decode", spy.held_by_caller()))
        return real_decode(body)

    def encode(msg: Message[BaseModel]) -> bytes:
        held.append(("encode", spy.held_by_caller()))
        return real_encode(msg)

    monkeypatch.setattr(codec, "_decode", decode)
    monkeypatch.setattr(codec, "_encode", encode)
    msg = _messages(codec, 0)[0]

    codec.decode(codec.encode(msg))
    with pytest.raises(DecodeError):
        codec.decode(b"{")  # the error path counts under the lock too
    codec.stats()

    assert held == [("encode", True), ("decode", True), ("decode", True), ("decode", True)]
    assert spy.acquisitions == 4  # encode, decode, failed decode, stats
