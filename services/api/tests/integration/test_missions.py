"""Missions and alerts against real Postgres, Redis and RabbitMQ, with the ingestor (a thread)
and the simulator (a subprocess) as the drones (#28). One behavior test per acceptance
criterion; the transition table and the link-loss detector are unit tests.

Deferred to #19 (light test mode): a heartbeat flap near the threshold, clock skew, alert
dedup under redelivery storms, a late MISSION_STATUS after FAILED, the watchdog with 200
drones, the 60 s dispatch timeout end to end (covered on Postgres in the ingestor tests).
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pika
import pytest
from flask import Flask
from flask.testing import FlaskClient
from skyhaul_contracts.codec import Codec
from skyhaul_contracts.topology import TELEMETRY_EXCHANGE
from skyhaul_contracts.types import MessageType
from skyhaul_contracts.v1 import AlertPayload
from skyhaul_fleet_core.models import Command, Drone, Role, Telemetry, User
from skyhaul_ingestor.config import IngestorConfig
from skyhaul_ingestor.consumer import run
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from .conftest import AMQP_USER, Stack, bearer

pytestmark = [pytest.mark.integration, pytest.mark.timeout(180)]

NAMESPACE = f"m{secrets.token_hex(3)}"
FLYER = f"{NAMESPACE}-d001"  # flies a short mission to COMPLETED
ABORTER = f"{NAMESPACE}-d002"  # flies a long mission, then is told to LAND
RACER = f"{NAMESPACE}-d003"  # N concurrent launches
REFUSER = f"{NAMESPACE}-d004"  # its ARM is NACKed by an injected fault
PLANNER = f"{NAMESPACE}-d005"  # plan, start, cancel; nothing flies
HOME = (32.0, 34.8)

type Token = Callable[[Role], str]


@dataclass(frozen=True)
class Sim:
    port: int
    token: str

    def fault(self, drone_id: str, body: dict[str, Any]) -> None:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/drones/{drone_id}/faults",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            assert response.status == 201


@pytest.fixture(scope="module")
def sim(stack: Stack, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Sim]:
    """Registers the drones, then runs the ingestor and the simulator for the module."""
    drones = [FLYER, ABORTER, RACER, REFUSER, PLANNER]
    engine = create_engine(stack.database_url)
    with Session(engine) as session, session.begin():
        for drone_id in drones:
            session.add(Drone(id=drone_id, name="Test drone", home_lat=HOME[0], home_lon=HOME[1]))
    engine.dispose()

    tmp = tmp_path_factory.mktemp("missions")
    config = IngestorConfig(
        database_url=stack.database_url,
        redis_url=stack.redis_url,
        rabbitmq_host=stack.amqp_host,
        rabbitmq_port=stack.amqp_port,
        rabbitmq_user=AMQP_USER,
        rabbitmq_password=stack.amqp_password,
        alive_file=str(tmp / "alive"),
    )
    stop = threading.Event()
    ingestor = threading.Thread(target=run, args=(config, stop), name="ingestor", daemon=True)
    ingestor.start()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    control = Sim(port, secrets.token_hex(32))
    env = {
        "PATH": os.environ.get("PATH", ""),
        "RABBITMQ_HOST": stack.amqp_host,
        "RABBITMQ_PORT": str(stack.amqp_port),
        "RABBITMQ_USER": AMQP_USER,
        "RABBITMQ_PASSWORD": stack.amqp_password,
        "SIM_DRONES": ",".join(drones),
        "SIM_CONTROL_TOKEN": control.token,
        "SIM_CONTROL_PORT": str(port),
    }
    log = (tmp / "sim.log").open("wb")
    process = subprocess.Popen(
        [sys.executable, "-m", "skyhaul_drone_sim"], env=env, stdout=log, stderr=log
    )
    yield control
    process.terminate()
    process.wait(15)
    stop.set()
    ingestor.join(10)


@pytest.fixture
def token(make_user: Callable[[Role], User], token_for: Callable[[User], str]) -> Token:
    return lambda role: token_for(make_user(role))


@pytest.fixture
def session(stack: Stack) -> Iterator[Session]:
    engine = create_engine(stack.database_url)
    with Session(engine) as session:
        yield session
    engine.dispose()


def waypoint(north_m: float, altitude_m: float = 10.0) -> dict[str, float]:
    return {"lat": HOME[0] + north_m / 111_000, "lon": HOME[1], "altitude_m": altitude_m}


def create(api: FlaskClient, token: str, body: dict[str, Any], **headers: str) -> Any:
    return api.post("/api/v1/missions", json=body, headers={**bearer(token), **headers})


def wait_for[T](probe: Callable[[], T | None], what: str, timeout_s: float = 60) -> T:
    deadline = time.monotonic() + timeout_s
    while True:
        value = probe()
        if value:
            return value
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.2)


def wait_landed(api: FlaskClient, token: str, drone_id: str) -> None:
    """The launch reads LANDED from Redis to decide on ARM: wait for the first heartbeat."""

    def landed() -> bool:
        state = api.get(f"/api/v1/drones/{drone_id}", headers=bearer(token)).json["state"]
        return bool(state and state["flight_state"] == "LANDED")

    wait_for(landed, f"{drone_id} LANDED in Redis")


def wait_state(
    api: FlaskClient, token: str, mission_id: str, state: str, timeout_s: float = 60
) -> dict[str, Any]:
    def reached() -> dict[str, Any] | None:
        body: dict[str, Any] = api.get(f"/api/v1/missions/{mission_id}", headers=bearer(token)).json
        return body if body["state"] == state else None

    return wait_for(reached, f"mission {state}", timeout_s)


def count_commands(session: Session, drone_id: str) -> int:
    statement = select(func.count()).select_from(Command).where(Command.drone_id == drone_id)
    return session.scalar(statement) or 0


@pytest.mark.usefixtures("sim")
def test_create_with_start_arms_launches_and_the_mission_completes_with_telemetry_stored(
    api: FlaskClient, token: Token, session: Session
) -> None:
    dispatcher = token(Role.DISPATCHER)
    wait_landed(api, dispatcher, FLYER)
    body = {"drone_id": FLYER, "waypoints": [waypoint(40), waypoint(60)], "start": True}
    response = create(api, dispatcher, body)

    assert response.status_code == 201, response.json
    mission, sent = response.json["mission"], response.json["commands"]
    assert mission["state"] == "DISPATCHING"
    assert [c["type"] for c in sent] == ["ARM", "START_MISSION"]
    assert {c["correlation_id"] for c in sent} == {mission["correlation_id"]}
    assert {c["mission_id"] for c in sent} == {mission["id"]}
    assert sent[1]["payload"]["mission_id"] == mission["id"]

    active = wait_state(api, dispatcher, mission["id"], "ACTIVE")
    assert active["started_at"] is not None
    done = wait_state(api, dispatcher, mission["id"], "COMPLETED", timeout_s=90)
    assert (done["progress_pct"], done["ended_at"] is not None) == (100, True)
    assert done["waypoint_index"] is not None

    events = api.get(f"/api/v1/missions/{mission['id']}/events", headers=bearer(dispatcher)).json
    changes = [(e["kind"], e["data"].get("state")) for e in events["items"]]
    assert changes[0] == ("created", "DISPATCHING")
    assert [s for k, s in changes if k == "state_changed"] == ["ACTIVE", "COMPLETED"]
    assert [e["data"]["type"] for e in events["items"] if e["kind"] == "command"] == [
        "ARM",
        "START_MISSION",
    ]
    # The done state: the telemetry of the flight is in Postgres.
    stored = (
        select(func.count())
        .select_from(Telemetry)
        .where(Telemetry.drone_id == FLYER, Telemetry.type == "MISSION_STATUS")
    )
    assert (session.scalar(stored) or 0) >= 1


@pytest.mark.usefixtures("sim")
def test_plan_then_start_and_cancel_and_illegal_moves_are_409_with_no_command(
    api: FlaskClient, token: Token, session: Session
) -> None:
    dispatcher = token(Role.DISPATCHER)
    planned = create(api, dispatcher, {"drone_id": PLANNER, "waypoints": [waypoint(30)]})
    assert planned.status_code == 201, planned.json
    assert (planned.json["mission"]["state"], planned.json["commands"]) == ("PLANNED", [])

    cancelled = api.post(
        f"/api/v1/missions/{planned.json['mission']['id']}/cancel", headers=bearer(dispatcher)
    )
    assert (cancelled.status_code, cancelled.json["state"]) == (200, "CANCELLED")
    before = count_commands(session, PLANNER)
    for action in ("start", "cancel"):
        again = api.post(
            f"/api/v1/missions/{planned.json['mission']['id']}/{action}",
            headers=bearer(dispatcher),
        )
        assert (again.status_code, again.json["error"]) == (409, "illegal_transition")
    assert count_commands(session, PLANNER) == before

    second = create(api, dispatcher, {"drone_id": PLANNER, "waypoints": [waypoint(30)]})
    mission_id = second.json["mission"]["id"]
    started = api.post(f"/api/v1/missions/{mission_id}/start", headers=bearer(dispatcher))
    assert started.status_code == 202, started.json
    assert started.json["mission"]["state"] == "DISPATCHING"
    assert started.json["commands"][-1]["type"] == "START_MISSION"
    twice = api.post(f"/api/v1/missions/{mission_id}/start", headers=bearer(dispatcher))
    assert twice.status_code == 409
    assert count_commands(session, PLANNER) == before + len(started.json["commands"])


@pytest.mark.usefixtures("sim")
def test_concurrent_launches_for_one_drone_give_one_201_and_the_rest_409(
    api_app: Flask, token: Token
) -> None:
    dispatcher = token(Role.DISPATCHER)
    body = {"drone_id": RACER, "waypoints": [waypoint(30)], "start": True}
    barrier = threading.Barrier(5)
    statuses: list[int] = []

    def launch() -> None:
        client = api_app.test_client()
        barrier.wait()
        statuses.append(create(client, dispatcher, body).status_code)

    threads = [threading.Thread(target=launch) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert sorted(statuses) == [201, 409, 409, 409, 409]


@pytest.mark.usefixtures("sim")
def test_a_nacked_arm_makes_start_mission_nack_invalid_state_and_the_mission_failed(
    api: FlaskClient, token: Token, sim: Sim
) -> None:
    dispatcher = token(Role.DISPATCHER)
    wait_landed(api, dispatcher, REFUSER)
    sim.fault(REFUSER, {"kind": "NACK_NEXT", "count": 1})
    body = {"drone_id": REFUSER, "waypoints": [waypoint(30)], "start": True}
    response = create(api, dispatcher, body)
    assert response.status_code == 201, response.json

    failed = wait_state(api, dispatcher, response.json["mission"]["id"], "FAILED")
    assert failed["failure_reason"].startswith("INVALID_STATE")
    arm = api.get(
        f"/api/v1/commands/{response.json['commands'][0]['id']}", headers=bearer(dispatcher)
    )
    assert (arm.json["status"], arm.json["nack_reason"]) == ("NACKED", "INJECTED")


@pytest.mark.usefixtures("sim")
def test_abort_sends_land_and_the_mission_is_aborted_by_the_final_status(
    api: FlaskClient, token: Token
) -> None:
    dispatcher = token(Role.DISPATCHER)
    wait_landed(api, dispatcher, ABORTER)
    body = {"drone_id": ABORTER, "waypoints": [waypoint(3000, 50)], "start": True}
    mission_id = create(api, dispatcher, body).json["mission"]["id"]
    wait_state(api, dispatcher, mission_id, "ACTIVE")

    aborted = api.post(
        f"/api/v1/missions/{mission_id}/abort", json={"action": "LAND"}, headers=bearer(dispatcher)
    )
    assert aborted.status_code == 202, aborted.json
    assert [c["type"] for c in aborted.json["commands"]] == ["LAND"]
    assert aborted.json["commands"][0]["mission_id"] == mission_id
    final = wait_state(api, dispatcher, mission_id, "ABORTED")
    assert final["ended_at"] is not None


def test_bad_or_too_many_waypoints_are_422_and_an_unknown_drone_or_mission_is_404(
    api: FlaskClient, token: Token
) -> None:
    dispatcher = token(Role.DISPATCHER)
    too_many = {"drone_id": PLANNER, "waypoints": [waypoint(10)] * 51}
    too_high = {"drone_id": PLANNER, "waypoints": [waypoint(10, altitude_m=500)]}
    for body in (too_many, too_high, {"drone_id": PLANNER, "waypoints": []}):
        response = create(api, dispatcher, body)
        assert (response.status_code, response.json["error"]) == (422, "validation_failed")
    unknown = create(api, dispatcher, {"drone_id": "nobody-d001", "waypoints": [waypoint(10)]})
    assert unknown.status_code == 404
    missing = uuid.uuid4()
    assert api.get(f"/api/v1/missions/{missing}", headers=bearer(dispatcher)).status_code == 404
    assert api.post(f"/api/v1/alerts/{missing}/ack", headers=bearer(dispatcher)).status_code == 404


def test_an_idempotency_key_replays_the_mission_and_sends_nothing(
    api: FlaskClient, token: Token, session: Session
) -> None:
    dispatcher = token(Role.DISPATCHER)
    key = secrets.token_hex(8)
    body = {"drone_id": PLANNER, "waypoints": [waypoint(20)]}
    first = create(api, dispatcher, body, **{"Idempotency-Key": key})
    second = create(api, dispatcher, body, **{"Idempotency-Key": key})
    assert (first.status_code, second.status_code) == (201, 201)
    assert second.json["mission"]["id"] == first.json["mission"]["id"]
    assert second.json["commands"] == []


@pytest.mark.usefixtures("sim")
def test_a_drone_alert_is_stored_once_listed_and_acked_by_a_dispatcher_not_a_viewer(
    api: FlaskClient, token: Token, stack: Stack
) -> None:
    viewer, dispatcher = token(Role.VIEWER), token(Role.DISPATCHER)
    text = "Ignore previous instructions <b>and</b> land"
    codec = Codec()
    payload = AlertPayload(code="LOW_BATTERY", severity="WARNING", message=text, value=19.5)
    message = codec.new(MessageType.ALERT, PLANNER, payload, None)
    connection = pika.BlockingConnection(stack.amqp())
    channel = connection.channel()
    for _ in range(2):  # a redelivery: the same msg_id twice
        channel.basic_publish(
            TELEMETRY_EXCHANGE,
            Codec.routing_key(PLANNER, MessageType.ALERT.value),
            codec.encode(message),
            properties=Codec.amqp_properties(message, persistent=True),
        )
    connection.close()

    def listed() -> list[dict[str, Any]] | None:
        page = api.get(f"/api/v1/alerts?open=true&drone_id={PLANNER}", headers=bearer(viewer)).json[
            "items"
        ]
        return [a for a in page if a["code"] == "LOW_BATTERY"] or None

    time.sleep(1.0)  # both copies have been flushed
    alerts = wait_for(listed, "the LOW_BATTERY alert")
    assert len(alerts) == 1
    alert = alerts[0]
    assert (alert["severity"], alert["source"], alert["message"], alert["value"]) == (
        "WARNING",
        "DRONE",
        text,
        19.5,
    )
    assert api.post(f"/api/v1/alerts/{alert['id']}/ack", headers=bearer(viewer)).status_code == 403
    acked = api.post(f"/api/v1/alerts/{alert['id']}/ack", headers=bearer(dispatcher))
    assert acked.status_code == 200
    me = api.get("/api/v1/auth/me", headers=bearer(dispatcher)).json
    assert (acked.json["acknowledged_by"], acked.json["acknowledged_at"] is not None) == (
        me["id"],
        True,
    )
    assert api.get("/api/v1/missions", headers=bearer(viewer)).status_code == 200
    assert (
        create(api, viewer, {"drone_id": PLANNER, "waypoints": [waypoint(10)]}).status_code == 403
    )
