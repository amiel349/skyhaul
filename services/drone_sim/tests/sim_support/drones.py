"""Drones and boards for the simulator tests."""

from typing import Any

from skyhaul_drone_sim.behaviors import add_drone, spawn_drone
from skyhaul_drone_sim.board import JobsBoard
from skyhaul_drone_sim.model import Drone, GeoPoint

HOME = GeoPoint(32.0, 34.8)
RADIUS_M = 5000.0
SEED = 7


def make_drone(drone_id: str = "main-d001", *, now: float = 0.0, **changes: Any) -> Drone:
    """A landed drone at HOME, without its jobs. `changes` set Drone fields."""
    drone, _ = spawn_drone(drone_id, home=HOME, now=now, seed=SEED, geofence_radius_m=RADIUS_M)
    for name, value in changes.items():
        setattr(drone, name, value)
    return drone


def board_with(*drone_ids: str) -> JobsBoard:
    """A board with bare drones (no jobs), for tests that bring their own jobs."""
    board = JobsBoard()
    for drone_id in drone_ids or ("main-d001", "main-d002"):
        board.add_drone(make_drone(drone_id))
    return board


def spawned_board(*drone_ids: str, now: float = 0.0, battery_pct: float = 100.0) -> JobsBoard:
    """A board with fully spawned drones: HEARTBEAT and BATTERY jobs included."""
    board = JobsBoard()
    with board.transaction() as world:
        for drone_id in drone_ids or ("main-d001",):
            drone, jobs = spawn_drone(
                drone_id,
                home=HOME,
                now=now,
                seed=SEED,
                geofence_radius_m=RADIUS_M,
                battery_pct=battery_pct,
            )
            add_drone(world, drone, jobs)
    return board
