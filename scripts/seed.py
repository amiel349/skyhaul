#!/usr/bin/env python3
"""`make seed`: a few registered drones, spawned in the simulator and sent on patrol missions.

Local test profile only. Needs `make up` and `make sim` running. Idempotent: a second run
creates nothing new. Secrets stay out of argv, logs and git:

- The admin is the dev admin that `make up` creates from DEV_ADMIN_EMAIL / DEV_ADMIN_PASSWORD in
  the git-ignored `.env.dev`. Its password is read through `docker compose config`, in memory only.
- The simulator control token is read inside the drone-sim container and never leaves it.
- `.env` is never parsed here; docker compose reads it, as in the Makefile.
"""

from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose", "--env-file", ".env"]
if os.environ.get("SKYHAUL_ENV", "dev") == "dev" and (ROOT / ".env.dev").exists():
    COMPOSE += ["--env-file", ".env.dev"]  # git-ignored local overrides, as in the Makefile
COMPOSE += ["-f", "deploy/compose.yaml"]
API = os.environ.get("SKYHAUL_API_URL", "http://127.0.0.1:8000").rstrip("/") + "/api/v1"
NAMESPACE = "dev"
DRONES = 3
# The one source of the demo area (Tel Aviv): drones start inside it and the map opens on it.
AREA: dict[str, Any] = json.loads((ROOT / "contracts/geo/demo-area.v1.json").read_text())
M_PER_DEG = 111_320.0

# Runs inside the drone-sim container: request on stdin, token from its own environment.
SIM_CALL = """
import json, os, sys, urllib.request
req = json.load(sys.stdin)
data = json.dumps(req["body"]).encode() if req.get("body") is not None else None
r = urllib.request.Request("http://127.0.0.1:8100" + req["path"], data=data, method=req["method"])
r.add_header("Authorization", "Bearer " + os.environ["SIM_CONTROL_TOKEN"])
if data:
    r.add_header("Content-Type", "application/json")
sys.stdout.write(urllib.request.urlopen(r, timeout=10).read().decode())
"""


def distance_m(a: dict[str, float], b: dict[str, float]) -> float:
    """Flat-earth distance in metres; accurate enough at city scale."""
    dlat = (a["lat"] - b["lat"]) * M_PER_DEG
    dlon = (a["lon"] - b["lon"]) * M_PER_DEG * math.cos(math.radians((a["lat"] + b["lat"]) / 2))
    return math.hypot(dlat, dlon)


def in_area(point: dict[str, float], area: dict[str, Any] = AREA) -> bool:
    bounds = area["bounds"]
    return bool(
        bounds["lat_min"] <= point["lat"] <= bounds["lat_max"]
        and bounds["lon_min"] <= point["lon"] <= bounds["lon_max"]
    )


def random_homes(
    count: int,
    area: dict[str, Any],
    rng: random.Random,
    min_separation_m: float = 400,
    max_tries: int = 200,
) -> list[dict[str, float]]:
    """`count` points uniformly inside the area bounds, at least `min_separation_m` apart.

    Rejection sampling with a bounded number of tries per point; when the area is too crowded the
    separation is relaxed (halved) so exactly `count` points always come back.
    """
    bounds = area["bounds"]
    points: list[dict[str, float]] = []
    separation = float(min_separation_m)
    while len(points) < count:
        for _ in range(max_tries):
            candidate = {
                "lat": round(rng.uniform(bounds["lat_min"], bounds["lat_max"]), 6),
                "lon": round(rng.uniform(bounds["lon_min"], bounds["lon_max"]), 6),
            }
            if all(distance_m(candidate, other) >= separation for other in points):
                points.append(candidate)
                break
        else:
            separation /= 2  # too crowded: relax and try again
    return points


def fail(message: str) -> Any:
    sys.exit(f"seed: {message}")


def compose(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed docker command, no user input
        [*COMPOSE, *args], cwd=ROOT, input=stdin, text=True, capture_output=True, check=False
    )


def sim(method: str, path: str, body: object = None) -> dict[str, Any]:
    request = json.dumps({"method": method, "path": path, "body": body})
    done = compose("exec", "-T", "drone-sim", "python", "-c", SIM_CALL, stdin=request)
    if done.returncode != 0:
        fail("the simulator control API did not answer; is `make sim` running?")
    result: dict[str, Any] = json.loads(done.stdout)
    return result


def api(
    method: str, path: str, body: object = None, token: str | None = None
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(API + path, data=data, method=method)  # noqa: S310 - local API URL
    if data:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except urllib.error.URLError:
        return 0, fail(f"cannot reach the API at {API}; is `make up` running?")


def dev_admin() -> tuple[str, str]:
    """The dev admin from .env, as docker compose resolves it. Parsed in memory, never printed.

    `make up` has already created or synced this admin (service dev-admin, profile dev).
    """
    done = compose("--profile", "dev", "config", "--format", "json")
    if done.returncode != 0:
        fail("docker compose could not read the stack config (run `make env` first)")
    env = json.loads(done.stdout)["services"]["dev-admin"]["environment"]
    email, password = env.get("DEV_ADMIN_EMAIL", ""), env.get("DEV_ADMIN_PASSWORD", "")
    if not email or not password:
        fail(
            "DEV_ADMIN_EMAIL / DEV_ADMIN_PASSWORD are empty; set them in .env.dev and run `make up`"
        )
    return email, password


def login(email: str, password: str) -> str:
    status, body = api("POST", "/auth/login", {"email": email, "password": password})
    if status != 200:
        fail(f"dev admin login failed (HTTP {status}); run `make up` to sync it from .env")
    return str(body["access_token"])


def patrol(index: int, home: dict[str, float]) -> list[dict[str, float]]:
    """Two laps of a circle around the drone's own home, 24 waypoints a lap (the API allows 50).

    Each drone gets its own radius, altitude and start angle so the markers do not overlap.
    At the simulator's 10 m/s cruise speed this is several minutes of movement.
    """
    radius_m = 300 + 150 * index
    lat0, lon0 = home["lat"], home["lon"]
    points = []
    for step in range(48):
        angle = math.radians(120 * index + (step % 24) * 15)
        points.append(
            {
                "lat": round(lat0 + radius_m * math.sin(angle) / M_PER_DEG, 6),
                "lon": round(
                    lon0 + radius_m * math.cos(angle) / (M_PER_DEG * math.cos(math.radians(lat0))),
                    6,
                ),
                "altitude_m": 40.0 + 10 * index,
            }
        )
    return points


def start_patrols(
    drones: list[dict[str, Any]], homes: dict[str, dict[str, float]], token: str
) -> None:
    """Launch a patrol mission for every drone that is not already flying one."""
    started = 0
    for index, drone in enumerate(drones):
        mission = drone.get("mission")
        if mission and mission.get("state") == "ACTIVE":
            continue  # an earlier run is still flying it
        drone_id = drone["drone_id"]
        body = {"drone_id": drone_id, "waypoints": patrol(index, homes[drone_id]), "start": True}
        status, _ = api("POST", "/missions", body, token)
        if status in (200, 201, 202):
            started += 1
        else:
            print(f"seed: could not start a patrol for {drone_id} (HTTP {status})")
    print(f"Started {started} patrol missions: the drones fly circles for several minutes.")


def registered_homes(token: str) -> dict[str, dict[str, float]]:
    """The home of every drone already in the fleet (first page: a seed run has a handful)."""
    status, body = api("GET", "/drones", None, token)
    if status != 200:
        return {}
    return {d["id"]: d["home"] for d in body.get("items", []) if d.get("home")}


def spawn(home: dict[str, float]) -> str:
    """Spawn one drone at `home`; every drone's geofence is centred on its own home."""
    body = {"namespace": NAMESPACE, "count": 1, "home": home}
    spawned = sim("POST", "/drones", body).get("drones", [])
    if len(spawned) != 1:
        fail("the simulator did not report the drone it spawned")
    return str(spawned[0]["drone_id"])


def main() -> None:
    email, password = dev_admin()
    token = login(email, password)

    existing = sim("GET", f"/drones?namespace={NAMESPACE}")["drones"]
    homes: dict[str, dict[str, float]] = {}
    missing = DRONES - len(existing)
    if missing > 0:
        for home in random_homes(missing, AREA, random.Random()):  # noqa: S311 - demo positions
            drone_id = spawn(home)
            homes[drone_id] = home
            print(f"Spawned {drone_id} at {home['lat']:.5f}, {home['lon']:.5f}")
    drones = sim("GET", f"/drones?namespace={NAMESPACE}")["drones"]

    fleet_homes = registered_homes(token)
    registered = 0
    for drone in drones:
        drone_id = drone["drone_id"]
        if drone_id not in homes:  # an earlier run's drone: never moved
            pos = drone.get("pos") or {}
            homes[drone_id] = fleet_homes.get(drone_id) or {"lat": pos["lat"], "lon": pos["lon"]}
        body = {"id": drone_id, "name": f"Seed {drone_id}", "home": homes[drone_id]}
        status, _ = api("POST", "/drones", body, token)
        if status == 201:
            registered += 1
        elif status != 409:  # 409 drone_id_taken: registered by an earlier run
            fail(f"could not register {drone_id} (HTTP {status})")
    print(
        f"Simulator: {len(drones)} drones in namespace '{NAMESPACE}'. Registered {registered} new."
    )
    if any(not in_area(home) for home in fleet_homes.values()):
        print(
            "Hint: some registered drones have a home outside the Tel Aviv area; "
            "`make down-volumes` then `make up` re-registers them inside it."
        )
    start_patrols(drones, homes, token)
    print(f"Sign in at the UI as {email}.")


if __name__ == "__main__":
    main()
