#!/usr/bin/env python3
"""`make seed`: a local admin, a few registered drones, the same drones spawned in the simulator.

Local test profile only. Needs `make up` and `make sim` running. Idempotent: a second run
creates nothing new. Secrets stay out of argv, logs and git:

- The admin is the dev admin that `make up` creates from DEV_ADMIN_EMAIL / DEV_ADMIN_PASSWORD in
  the git-ignored `.env.dev`. Its password is read through `docker compose config`, in memory only.
- The simulator control token is read inside the drone-sim container and never leaves it.
- `.env` is never parsed here; docker compose reads it, as in the Makefile.
"""

from __future__ import annotations

import json
import os
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
DEFAULT_HOME = {"lat": 32.0, "lon": 34.8}  # the simulator's default SIM_HOME_*

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
    method: str, path: str, body: object, token: str | None = None
) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(API + path, data=json.dumps(body).encode(), method=method)  # noqa: S310 - local API URL
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


def main() -> None:
    email, password = dev_admin()
    token = login(email, password)

    existing = sim("GET", f"/drones?namespace={NAMESPACE}")["drones"]
    if len(existing) < DRONES:
        sim("POST", "/drones", {"namespace": NAMESPACE, "count": DRONES - len(existing)})
    drones = sim("GET", f"/drones?namespace={NAMESPACE}")["drones"]

    registered = 0
    for drone in drones:
        drone_id = drone["drone_id"]
        body = {"id": drone_id, "name": f"Seed {drone_id}", "home": DEFAULT_HOME}
        status, _ = api("POST", "/drones", body, token)
        if status == 201:
            registered += 1
        elif status != 409:  # 409 drone_id_taken: registered by an earlier run
            fail(f"could not register {drone_id} (HTTP {status})")
    print(
        f"Simulator: {len(drones)} drones in namespace '{NAMESPACE}'. Registered {registered} new."
    )
    print(f"Sign in at the UI as {email} (password: DEV_ADMIN_PASSWORD in .env).")


if __name__ == "__main__":
    main()
