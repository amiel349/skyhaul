"""The control API through the Flask test client against a real board, with no broker.
One test per acceptance criterion of #9 (unit layer)."""

import logging
import os
import secrets
import subprocess
import sys
from typing import Any

import pytest
from flask.testing import FlaskClient
from sim_support.config import sim_config
from sim_support.drones import make_drone
from sim_support.fakes import FakeBroker
from sim_support.messages import command
from sim_support.running import running_simulator
from skyhaul_contracts import Codec
from skyhaul_drone_sim.board import JobsBoard
from skyhaul_drone_sim.clock import FakeClock
from skyhaul_drone_sim.control_api import create_app
from skyhaul_drone_sim.handlers import registry
from skyhaul_drone_sim.logs import JsonFormatter
from skyhaul_drone_sim.simulator import Simulator

CFG = sim_config()
AUTH = {"Authorization": f"Bearer {CFG.control_token}"}


@pytest.fixture
def board() -> JobsBoard:
    return JobsBoard()


@pytest.fixture
def api(board: JobsBoard) -> FlaskClient:
    app = create_app(board, CFG, clock=FakeClock(), health=lambda: True, stats=lambda: {})
    return app.test_client()


def spawn(api: FlaskClient, namespace: str, count: int, **extra: Any) -> list[str]:
    reply = api.post(
        "/drones", json={"namespace": namespace, "count": count, **extra}, headers=AUTH
    )
    assert reply.status_code == 201, reply.json
    return [drone["drone_id"] for drone in reply.json["drones"]]  # type: ignore[index]


def ids(api: FlaskClient, namespace: str) -> list[str]:
    reply = api.get(f"/drones?namespace={namespace}", headers=AUTH)
    assert reply.status_code == 200
    return [drone["drone_id"] for drone in reply.json["drones"]]  # type: ignore[index]


# --- access ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Bearer",
        "Bearer wrong",
        f"Basic {CFG.control_token}",
        f"Bearer {CFG.control_token}x",
    ],
)
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/drones", {"namespace": "w1", "count": 1}),
        ("GET", "/drones?namespace=w1", None),
        ("GET", "/drones/w1-d001", None),
        ("GET", "/drones/w1-d999", None),
        ("DELETE", "/drones/w1-d001", None),
        ("POST", "/reset", {"namespace": "w1"}),
        ("GET", "/stats", None),
        ("GET", "/no-such-route", None),
    ],
)
def test_no_or_wrong_token_gets_401_changes_nothing_and_never_logs_the_token(
    api: FlaskClient,
    board: JobsBoard,
    caplog: pytest.LogCaptureFixture,
    authorization: str | None,
    method: str,
    path: str,
    body: Any,
) -> None:
    spawn(api, "w1", 1)
    caplog.clear()
    headers = {} if authorization is None else {"Authorization": authorization}

    with caplog.at_level(logging.DEBUG):
        reply = api.open(path, method=method, json=body, headers=headers)

    assert reply.status_code == 401
    assert reply.json == {"error": "unauthorized", "detail": "a valid bearer token is required"}
    assert ids(api, "w1") == ["w1-d001"]  # nothing changed
    assert board.is_current("w1", 0)  # no reset happened
    logged = "\n".join(JsonFormatter().format(record) for record in caplog.records)
    assert caplog.records  # the request was logged
    assert CFG.control_token not in logged
    assert "authorization" not in logged.lower()
    assert CFG.control_token not in reply.get_data(as_text=True)


@pytest.mark.parametrize(
    ("token", "error"),
    [
        (None, "SIM_CONTROL_TOKEN must be set"),
        ("x" * 31, "SIM_CONTROL_TOKEN must be at least 32 characters"),
    ],
)
def test_unset_or_short_token_refuses_to_start_naming_the_variable(
    token: str | None, error: str
) -> None:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "RABBITMQ_HOST": "127.0.0.1",
        "RABBITMQ_USER": "sim",
        "RABBITMQ_PASSWORD": secrets.token_hex(8),
    }
    if token is not None:
        env["SIM_CONTROL_TOKEN"] = token

    done = subprocess.run(
        [sys.executable, "-m", "skyhaul_drone_sim"],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert done.returncode == 2
    assert error in done.stderr
    if token is not None:
        assert token not in done.stderr


# --- health and stats ------------------------------------------------------------------


def test_healthz_is_200_ok_without_token_when_running_and_503_degraded_when_a_part_is_down() -> (
    None
):
    with running_simulator() as running:
        assert running.client.call("GET", "/healthz", token="").body == {"status": "ok"}

        running.sim.publisher.connected.clear()  # the publisher connection is down
        down = running.client.call("GET", "/healthz", token="")
        running.sim.publisher.connected.set()

        assert (down.status, down.body) == (503, {"status": "degraded"})
        assert running.client.call("GET", "/healthz", token="").status == 200


def test_stats_counts_commands_for_unknown_drones_and_has_every_counter() -> None:
    sim = Simulator(
        CFG,
        registry=registry,
        consumer_connect=FakeBroker().connect,
        publisher_connect=FakeBroker().connect,
    )
    api = sim.app.test_client()
    before = api.get("/stats", headers=AUTH).json
    assert before is not None

    for _ in range(3):
        msg = command("ARM", drone_id="w1-d042")
        sim.consumer.handle("drone.w1-d042.ARM", Codec().encode(msg))
    after = api.get("/stats", headers=AUTH).json

    assert after is not None
    assert after["unknown_drone_commands"] == before["unknown_drone_commands"] + 3
    assert set(after) == {
        "outbox_depth",
        "published",
        "dropped_stale",
        "encode_errors",
        "unknown_drone_commands",
        "nacks_by_reason",
        "codec_rejects",
    }
    assert after["nacks_by_reason"]["INVALID_STATE"] == 0


# --- spawn and state -------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"namespace": "w1", "count": 0},
        {"namespace": "w1", "count": 101},
        {"namespace": "w1", "count": 1, "battery_pct": -1},
        {"namespace": "w1", "count": 1, "battery_pct": 100.5},
        {"namespace": "W1", "count": 1},
        {"namespace": "w-1", "count": 1},
        {"namespace": "a" * 17, "count": 1},
    ],
)
def test_existing_ids_get_409_and_out_of_range_values_get_400(
    api: FlaskClient, board: JobsBoard, body: dict[str, Any]
) -> None:
    board.add_drone(make_drone("w1-d001"))  # seeded at startup, like SIM_DRONES

    conflict = api.post("/drones", json={"namespace": "w1", "count": 2}, headers=AUTH)
    invalid = api.post("/drones", json=body, headers=AUTH)

    assert conflict.status_code == 409
    assert set(conflict.json or {}) == {"error", "detail"}
    assert invalid.status_code == 400
    assert set(invalid.json or {}) == {"error", "detail"}
    assert ids(api, "w1") == ["w1-d001"]


def test_listing_a_namespace_shows_only_its_drones_with_their_state(api: FlaskClient) -> None:
    spawn(api, "w1", 2)
    spawn(api, "w2", 1)
    spawn(api, "gw1", 1)
    spawn(api, "gw10", 1)

    reply = api.get("/drones?namespace=w1", headers=AUTH).json

    assert reply is not None
    assert [d["drone_id"] for d in reply["drones"]] == ["w1-d001", "w1-d002"]
    assert ids(api, "gw1") == ["gw1-d001"]  # never gw10's
    first = reply["drones"][0]
    assert first == {
        "drone_id": "w1-d001",
        "state": "LANDED",
        "armed": False,
        "pos": {"lat": CFG.home_lat, "lon": CFG.home_lon, "altitude_m": 0.0},
        "battery_pct": 100.0,
        "mission": None,
        "faults": [],
    }


def test_getting_a_drone_shows_its_state_and_jobs_and_an_unknown_id_is_404(
    api: FlaskClient,
) -> None:
    spawn(api, "w1", 1, battery_pct=80, home={"lat": 10.0, "lon": 20.0})

    drone = api.get("/drones/w1-d001", headers=AUTH).json
    missing = api.get("/drones/w1-d002", headers=AUTH)

    assert drone is not None
    assert drone["pos"] == {"lat": 10.0, "lon": 20.0, "altitude_m": 0.0}
    assert (drone["battery_pct"], drone["target"], drone["cruise_mps"]) == (80.0, None, 10.0)
    assert drone["jobs"] == [
        {"type": "HEARTBEAT", "period_s": 1.0, "next_due_in_s": 1.0},
        {"type": "BATTERY", "period_s": 5.0, "next_due_in_s": 5.0},
    ]
    assert {"state", "mission", "faults"} <= set(drone)
    assert missing.status_code == 404


def test_deleting_a_drone_removes_it_and_its_jobs_and_an_unknown_id_is_404(
    api: FlaskClient, board: JobsBoard
) -> None:
    spawn(api, "w1", 2)

    deleted = api.delete("/drones/w1-d001", headers=AUTH)
    again = api.delete("/drones/w1-d001", headers=AUTH)

    assert (deleted.status_code, deleted.data) == (204, b"")
    assert again.status_code == 404
    assert ids(api, "w1") == ["w1-d002"]
    emitted = board.due(now=10.0, dt=0.1).messages  # it sends no more telemetry
    assert {out.message.drone_id for out in emitted} == {"w1-d002"}


# --- reset -----------------------------------------------------------------------------


def test_reset_namespace_spawns_again_from_d001(api: FlaskClient) -> None:
    spawn(api, "w1", 3)
    spawn(api, "w2", 1)

    reply = api.post("/reset", json={"namespace": "w1"}, headers=AUTH)

    assert reply.json == {"namespace": "w1", "drones_removed": 3, "jobs_removed": 6, "epoch": 1}
    assert ids(api, "w1") == []
    assert ids(api, "w2") == ["w2-d001"]
    assert spawn(api, "w1", 1) == ["w1-d001"]


@pytest.mark.parametrize("body", [{}, {"namespace": "*"}, {"namespace": ""}, {"namespace": None}])
def test_empty_namespace_resets_twice_and_missing_or_wildcard_is_400(
    api: FlaskClient, body: dict[str, Any]
) -> None:
    first = api.post("/reset", json={"namespace": "empty"}, headers=AUTH)
    second = api.post("/reset", json={"namespace": "empty"}, headers=AUTH)
    invalid = api.post("/reset", json=body, headers=AUTH)

    assert (first.status_code, first.json["epoch"]) == (200, 1)  # type: ignore[index]
    assert (second.status_code, second.json["epoch"]) == (200, 2)  # type: ignore[index]
    assert invalid.status_code == 400
    assert set(invalid.json or {}) == {"error", "detail"}
