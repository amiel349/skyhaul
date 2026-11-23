"""Clocks: the system clock, and a controlled clock for tests."""

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol

_NS_PER_S = 1_000_000_000
_DEFAULT_WALL_START = datetime(2026, 1, 1, tzinfo=UTC)


class Clock(Protocol):
    def monotonic(self) -> float:
        """Seconds on a clock that never goes back. Used for periods and deadlines."""
        ...

    def utcnow(self) -> datetime:
        """Timezone-aware UTC wall time. Used for message timestamps only."""
        ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def utcnow(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """A clock that moves only when `advance` is called.

    Time is kept in whole nanoseconds, so ten steps of 0.1 s land exactly on 1.0 s
    instead of 0.9999999999999999. Safe to read and advance from several threads.
    """

    def __init__(self, start_s: float = 0.0, wall_start: datetime = _DEFAULT_WALL_START) -> None:
        if wall_start.tzinfo is None:
            raise ValueError("wall_start must be timezone-aware")
        self._lock = threading.Lock()
        self._start_ns = round(start_s * _NS_PER_S)
        self._now_ns = self._start_ns
        self._wall_start = wall_start

    def monotonic(self) -> float:
        with self._lock:
            return self._now_ns / _NS_PER_S

    def utcnow(self) -> datetime:
        with self._lock:
            elapsed_us = (self._now_ns - self._start_ns) // 1000
        return self._wall_start + timedelta(microseconds=elapsed_us)

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("a monotonic clock cannot go back")
        with self._lock:
            self._now_ns += round(seconds * _NS_PER_S)
