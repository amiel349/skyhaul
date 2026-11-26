"""`random_homes` of the seed script: pure, so it runs without the stack."""

from __future__ import annotations

import importlib.util
import random
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("seed", Path(__file__).with_name("seed.py"))
assert _SPEC
assert _SPEC.loader
seed = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(seed)

AREA = seed.AREA


def test_random_homes_stay_inside_the_area_and_keep_their_distance() -> None:
    homes = seed.random_homes(5, AREA, random.Random(7))

    assert len(homes) == 5
    assert all(seed.in_area(home) for home in homes)
    pairs = [(a, b) for i, a in enumerate(homes) for b in homes[i + 1 :]]
    assert all(seed.distance_m(a, b) >= 400 for a, b in pairs)


def test_random_homes_are_deterministic_for_a_seeded_rng() -> None:
    first = seed.random_homes(3, AREA, random.Random(1))

    assert first == seed.random_homes(3, AREA, random.Random(1))


def test_random_homes_relax_the_separation_instead_of_failing() -> None:
    homes = seed.random_homes(30, AREA, random.Random(3), min_separation_m=50_000)

    assert len(homes) == 30
    assert all(seed.in_area(home) for home in homes)


def test_only_drones_with_a_live_state_are_ready_for_a_mission() -> None:
    items = [
        {"id": "d1", "state": {"flight_state": "LANDED"}},
        {"id": "d2", "state": None},
        {"id": "d3", "state": {"flight_state": None}},
        {"id": "d4"},
    ]

    assert seed.drones_with_state(items) == {"d1"}
