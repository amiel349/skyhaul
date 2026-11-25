#!/usr/bin/env python3
"""`make seed`: a local admin, a few registered drones, the same drones spawned in the simulator.

Local test profile only. Needs `make up` and `make sim` running. Idempotent: a second run
creates nothing new. Secrets stay out of argv, logs and git:

- The admin password is generated once, printed once and kept in `secrets/seed-admin-password`
  (git-ignored, mode 600) so a rerun can log in. `SEED_ADMIN_PASSWORD` overrides the file.
- The simulator control token is read inside the drone-sim container and never leaves it.
- `.env` is never read here; docker compose reads it, as in the Makefile.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose", "--env-file", ".env", "-f", "deploy/compose.yaml"]
API = os.environ.get("SKYHAUL_API_URL", "http://127.0.0.1:8000").rstrip("/") + "/api/v1"
ADMIN_EMAIL = os.environ.get("SEED_ADMIN_EMAIL", "admin@skyhaul.dev")
NAMESPACE = "dev"
DRONES = 3
DEFAULT_HOME = {"lat": 32.0, "lon": 34.8}  # the simulator's default SIM_HOME_*
PASSWORD_FILE = ROOT / "secrets" / "seed-admin-password"

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


def admin_password() -> tuple[str, bool]:
    """The password and whether it is new (to be created and printed)."""
    given = os.environ.get("SEED_ADMIN_PASSWORD")
    if given:
        return given, False
    if PASSWORD_FILE.exists():
        return PASSWORD_FILE.read_text().strip(), False
    return secrets.token_urlsafe(24), True


def ensure_admin(password: str, is_new: bool) -> None:
    if not is_new:
        return  # an earlier run (or the caller) created the admin
    done = compose(
        "exec",
        "-T",
        "api",
        "flask",
        "--app",
        "skyhaul_api:create_app()",
        "skyhaul",
        "bootstrap",
        "--admin-email",
        ADMIN_EMAIL,
        stdin=password + "\n",
    )
    if done.returncode != 0:
        if "exists" in done.stderr:
            fail(
                f"{ADMIN_EMAIL} already exists but its password is not in "
                f"{PASSWORD_FILE.relative_to(ROOT)}; set SEED_ADMIN_PASSWORD to it"
            )
        fail("could not create the admin (is the api container up?)")
    PASSWORD_FILE.parent.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(PASSWORD_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(password + "\n")
    print(f"Created admin {ADMIN_EMAIL}")
    print(f"Password (shown once, also saved in {PASSWORD_FILE.relative_to(ROOT)}): {password}")


def login(password: str) -> str:
    status, body = api("POST", "/auth/login", {"email": ADMIN_EMAIL, "password": password})
    if status != 200:
        fail(f"admin login failed (HTTP {status})")
    return str(body["access_token"])


def main() -> None:
    password, is_new = admin_password()
    ensure_admin(password, is_new)
    token = login(password)

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
    print(f"Sign in at the UI as {ADMIN_EMAIL}.")


if __name__ == "__main__":
    main()
