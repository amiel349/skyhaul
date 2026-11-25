"""The link-loss detector on a fake clock (#28): one LINK_LOST after 5 s of silence, one
clear on the next heartbeat, only drones that sent a heartbeat are watched."""

from __future__ import annotations

from skyhaul_ingestor.watchdog import LinkDetector


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


def test_five_seconds_of_silence_is_one_loss_and_the_next_heartbeat_one_clear() -> None:
    clock = FakeClock()
    detector = LinkDetector(after_s=5)
    detector.heard(["a-d001", "a-d002"], clock.now)

    assert detector.silent(clock.advance(4.9)) == []
    detector.heard(["a-d002"], clock.now)  # d002 keeps talking
    assert detector.silent(clock.advance(0.1)) == ["a-d001"]
    detector.mark_lost("a-d001")
    assert detector.silent(clock.advance(10)) == ["a-d002"]  # d001 is not raised twice
    detector.mark_lost("a-d002")
    assert detector.silent(clock.advance(10)) == []

    detector.heard(["a-d001"], clock.now)
    assert detector.returned() == ["a-d001"]
    detector.mark_up("a-d001")
    assert detector.returned() == []
    # Watched again from its new heartbeat.
    assert detector.silent(clock.advance(5)) == ["a-d001"]


def test_unheard_drones_are_not_watched_and_a_seeded_loss_clears_on_heartbeat() -> None:
    clock = FakeClock()
    detector = LinkDetector(after_s=5)
    detector.seed_lost(["a-d003"])  # open LINK_LOST from before a restart
    assert detector.silent(clock.advance(60)) == []
    detector.heard(["a-d003"], clock.now)
    assert detector.returned() == ["a-d003"]
