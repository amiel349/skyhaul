from datetime import UTC, datetime, timedelta

import pytest
from skyhaul_drone_sim.clock import FakeClock, SystemClock


def test_fake_clock_ten_steps_of_a_tenth_land_exactly_on_one_second() -> None:
    clock = FakeClock()

    for _ in range(10):
        clock.advance(0.1)

    assert clock.monotonic() == 1.0


def test_fake_clock_wall_time_moves_with_monotonic_time() -> None:
    start = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)
    clock = FakeClock(start_s=100.0, wall_start=start)

    clock.advance(2.5)

    assert clock.monotonic() == 102.5
    assert clock.utcnow() == start + timedelta(seconds=2.5)


def test_fake_clock_cannot_go_back() -> None:
    clock = FakeClock()

    with pytest.raises(ValueError, match="cannot go back"):
        clock.advance(-0.1)


def test_fake_clock_rejects_naive_wall_start() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FakeClock(wall_start=datetime(2026, 1, 1))


def test_system_clock_wall_time_is_utc_aware() -> None:
    assert SystemClock().utcnow().tzinfo is UTC
