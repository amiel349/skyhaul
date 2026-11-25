"""The command service against real Postgres, Redis and RabbitMQ, with the ingestor (a thread,
command timeout 2 s) applying replies and the simulator (a subprocess) as the drones (#26).

Deferred to #19 (light test mode): redelivery storms, a broker restart mid-flight, confirm
timeout tuning, a crash between commit and publish beyond the 30 s rule, a real broker nack
(the nack retry is covered by the unit tests with a fake channel).
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pika
import pytest
from flask import Flask
from flask.testing import FlaskClient
from skyhaul_api import create_app
from skyhaul_api.commands.routes import PUBLISHER_KEY
from skyhaul_contracts.codec import Codec
from skyhaul_contracts.topology import COMMAND_EXCHANGE, TELEMETRY_EXCHANGE
from skyhaul_contracts.types import MessageType
from skyhaul_contracts.v1 import AckPayload, NackPayload
from skyhaul_fleet_core.models import AuditEntry, Command, Drone, Role, User
from skyhaul_ingestor.config import IngestorConfig
from skyhaul_ingestor.consumer import run
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from .conftest import AMQP_USER, Stack, bearer

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]

codec = Codec()
NAMESPACE = f"c{secrets.token_hex(3)}"
ACKING = f"{NAMESPACE}-d001"  # LANDED in the simulator: ARM is accepted
NACKING = f"{NAMESPACE}-d002"  # disarmed: TAKEOFF is refused with INVALID_STATE
SILENT = f"{NAMESPACE}-d099"  # registered here, unknown to the simulator: no reply
OTHER = f"{NAMESPACE}-d003"
TIMEOUT_S = 2

type Token = Callable[[Role], str]


@pytest.fixture(scope="module")
def fleet(stack: Stack, tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Registers the drones, then runs the ingestor and the simulator for the module."""
    engine = create_engine(stack.database_url)
    with Session(engine) as session, session.begin():
        for drone_id in (ACKING, NACKING, SILENT, OTHER):
            session.add(Drone(id=drone_id, name="Test drone", home_lat=32.0, home_lon=34.8))
    engine.dispose()

    tmp = tmp_path_factory.mktemp("fleet")
    config = IngestorConfig(
        database_url=stack.database_url,
        redis_url=stack.redis_url,
        rabbitmq_host=stack.amqp_host,
        rabbitmq_port=stack.amqp_port,
        rabbitmq_user=AMQP_USER,
        rabbitmq_password=stack.amqp_password,
        command_timeout_s=TIMEOUT_S,
        alive_file=str(tmp / "alive"),
    )
    stop = threading.Event()
    ingestor = threading.Thread(target=run, args=(config, stop), name="ingestor", daemon=True)
    ingestor.start()
    sim = _start_sim(stack, [ACKING, NACKING, OTHER], tmp / "sim.log")
    yield
    sim.terminate()
    sim.wait(15)
    stop.set()
    ingestor.join(10)


def _start_sim(stack: Stack, drones: list[str], log_path: Path) -> subprocess.Popen[bytes]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        "PATH": os.environ.get("PATH", ""),
        "RABBITMQ_HOST": stack.amqp_host,
        "RABBITMQ_PORT": str(stack.amqp_port),
        "RABBITMQ_USER": AMQP_USER,
        "RABBITMQ_PASSWORD": stack.amqp_password,
        "SIM_DRONES": ",".join(drones),
        "SIM_CONTROL_TOKEN": secrets.token_hex(32),
        "SIM_CONTROL_PORT": str(port),
    }
    log = log_path.open("wb")
    return subprocess.Popen(
        [sys.executable, "-m", "skyhaul_drone_sim"], env=env, stdout=log, stderr=log
    )


@pytest.fixture
def token(make_user: Callable[[Role], User], token_for: Callable[[User], str]) -> Token:
    return lambda role: token_for(make_user(role))


@pytest.fixture
def session(stack: Stack) -> Iterator[Session]:
    engine = create_engine(stack.database_url)
    with Session(engine) as session:
        yield session
    engine.dispose()


def send(api: FlaskClient, token: str, drone_id: str, body: dict[str, Any], **headers: str) -> Any:
    return api.post(
        f"/api/v1/drones/{drone_id}/commands", json=body, headers={**bearer(token), **headers}
    )


def wait_status(
    api: FlaskClient, token: str, command_id: str, status: str, timeout_s: float = 30
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while True:
        body: dict[str, Any] = api.get(f"/api/v1/commands/{command_id}", headers=bearer(token)).json
        if body["status"] == status:
            return body
        assert time.monotonic() < deadline, f"command stayed {body['status']}, wanted {status}"
        time.sleep(0.2)


def count_commands(session: Session, drone_id: str) -> int:
    statement = select(func.count()).select_from(Command).where(Command.drone_id == drone_id)
    return session.scalar(statement) or 0


def publish_reply(stack: Stack, drone_id: str, kind: MessageType, payload: Any) -> None:
    """A drone reply sent by the test through the Codec (for late and mismatched replies)."""
    msg = codec.new(kind, drone_id, payload, str(uuid.uuid4()))
    connection = pika.BlockingConnection(stack.amqp())
    connection.channel().basic_publish(
        TELEMETRY_EXCHANGE,
        Codec.routing_key(drone_id, kind.value),
        codec.encode(msg),
        properties=Codec.amqp_properties(msg, persistent=True),
    )
    connection.close()


@pytest.mark.usefixtures("fleet")
def test_a_dispatcher_command_is_stored_audited_confirmed_and_acked_by_the_drone(
    api: FlaskClient, token: Token, session: Session
) -> None:
    dispatcher = token(Role.DISPATCHER)
    request_id = str(uuid.uuid4())
    response = send(api, dispatcher, ACKING, {"type": "ARM"}, **{"X-Request-ID": request_id})

    assert response.status_code == 202, response.json
    created = response.json
    # Broker-confirmed before the answer; the ACK may already have overtaken it.
    assert created["status"] in {"SENT", "ACKED"}
    assert (created["drone_id"], created["type"], created["correlation_id"]) == (
        ACKING,
        "ARM",
        request_id,
    )
    assert created["sent_at"] is not None or created["status"] == "ACKED"

    acked = wait_status(api, dispatcher, created["id"], "ACKED")
    assert (acked["late"], acked["nack_reason"], acked["replied_at"] is not None) == (
        False,
        None,
        True,
    )
    listed = api.get(f"/api/v1/drones/{ACKING}/commands", headers=bearer(dispatcher)).json
    assert created["id"] in [c["id"] for c in listed["items"]]

    row = session.get(Command, uuid.UUID(created["id"]))
    assert row is not None
    assert str(row.requested_by) == created["requested_by"]
    audit = session.scalars(select(AuditEntry).where(AuditEntry.target_id == created["id"])).all()
    assert [(a.action, a.actor_user_id) for a in audit] == [("command.send", row.requested_by)]


@pytest.mark.usefixtures("fleet")
def test_a_refused_command_is_nacked_with_reason_and_detail(api: FlaskClient, token: Token) -> None:
    dispatcher = token(Role.DISPATCHER)
    response = send(api, dispatcher, NACKING, {"type": "TAKEOFF", "payload": {"altitude_m": 20}})
    assert response.status_code == 202, response.json
    nacked = wait_status(api, dispatcher, response.json["id"], "NACKED")
    assert nacked["nack_reason"] == "INVALID_STATE"
    assert isinstance(nacked["nack_detail"], str)
    assert nacked["nack_detail"]


@pytest.mark.parametrize(
    "body",
    [
        {"type": "SPIN"},
        {"type": "TAKEOFF", "payload": {"altitude_m": 987654}},
        {"type": "GOTO", "payload": {"lat": 32.0, "lon": 34.8}},
        {"type": "START_MISSION", "payload": {}},
    ],
)
def test_a_contract_violation_or_start_mission_is_422_and_nothing_is_stored(
    api: FlaskClient, token: Token, session: Session, body: dict[str, Any]
) -> None:
    drone_id = f"v{secrets.token_hex(3)}-d001"
    session.add(Drone(id=drone_id, name="Test drone", home_lat=32.0, home_lon=34.8))
    session.commit()
    response = send(api, token(Role.DISPATCHER), drone_id, body)
    assert response.status_code == 422
    assert response.json["error"] == "validation_failed"
    assert response.json["fields"]
    assert "987654" not in response.get_data(as_text=True)
    assert count_commands(session, drone_id) == 0


def test_viewers_get_403_unknown_drones_404_and_a_31st_command_a_minute_429(
    api: FlaskClient, token: Token, session: Session
) -> None:
    viewer = token(Role.VIEWER)
    assert send(api, viewer, ACKING, {"type": "ARM"}).status_code == 403

    dispatcher = token(Role.DISPATCHER)
    missing = f"m{secrets.token_hex(3)}-d001"
    statuses = [send(api, dispatcher, missing, {"type": "ARM"}).status_code for _ in range(31)]
    assert statuses[:30] == [404] * 30
    assert count_commands(session, missing) == 0

    limited = send(api, dispatcher, missing, {"type": "ARM"})
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0
    # Per user: another dispatcher is not limited.
    assert send(api, token(Role.DISPATCHER), missing, {"type": "ARM"}).status_code == 404


@pytest.mark.usefixtures("fleet")
def test_the_same_idempotency_key_returns_the_original_and_publishes_nothing(
    api: FlaskClient,
    token: Token,
    session: Session,
    api_app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = token(Role.DISPATCHER)
    publisher = api_app.extensions[PUBLISHER_KEY]
    submitted: list[str] = []
    original_submit = publisher.submit

    def counting_submit(message: Any, body: bytes) -> Any:
        submitted.append(message.msg_id)
        return original_submit(message, body)

    monkeypatch.setattr(publisher, "submit", counting_submit)
    key = secrets.token_hex(8)
    first = send(api, dispatcher, OTHER, {"type": "ARM"}, **{"Idempotency-Key": key})
    second = send(api, dispatcher, OTHER, {"type": "ARM"}, **{"Idempotency-Key": key})
    without_key = [send(api, dispatcher, OTHER, {"type": "ARM"}) for _ in range(2)]

    goto_key = secrets.token_hex(8)
    goto = {"type": "GOTO", "payload": {"lat": 32.0, "lon": 34.8, "altitude_m": 30}}
    elsewhere = {"type": "GOTO", "payload": {"lat": 32.1, "lon": 34.8, "altitude_m": 30}}
    assert send(api, dispatcher, SILENT, goto, **{"Idempotency-Key": goto_key}).status_code == 202
    reused = send(api, dispatcher, SILENT, elsewhere, **{"Idempotency-Key": goto_key})
    assert (reused.status_code, reused.json["error"]) == (409, "idempotency_key_reused")

    assert (first.status_code, second.status_code) == (202, 202)
    assert second.json["id"] == first.json["id"]
    assert len(submitted) == 4  # the first keyed one, two without a key, the first GOTO
    assert len({r.json["id"] for r in without_key}) == 2
    keyed = select(func.count()).select_from(Command).where(Command.idempotency_key == key)
    assert session.scalar(keyed) == 1


@pytest.mark.usefixtures("fleet")
def test_no_reply_times_out_and_a_late_reply_wins_while_duplicates_and_strangers_do_not(
    api: FlaskClient, token: Token, stack: Stack
) -> None:
    dispatcher = token(Role.DISPATCHER)
    response = send(api, dispatcher, SILENT, {"type": "ARM"})
    assert response.status_code == 202, response.json
    command_id = response.json["id"]
    timed_out = wait_status(api, dispatcher, command_id, "TIMED_OUT", timeout_s=TIMEOUT_S + 10)
    assert timed_out["replied_at"] is None

    # Another drone naming this command changes nothing.
    ack = AckPayload(command_msg_id=command_id, command_type="ARM")
    publish_reply(stack, OTHER, MessageType.ACK, ack)
    # The real drone answers late: it wins, with late=true.
    publish_reply(stack, SILENT, MessageType.ACK, ack)
    acked = wait_status(api, dispatcher, command_id, "ACKED")
    assert acked["late"] is True

    # A later NACK for the same command never moves it backwards.
    nack = NackPayload(
        command_msg_id=command_id, command_type="ARM", reason="BATTERY_TOO_LOW", detail="late"
    )
    publish_reply(stack, SILENT, MessageType.NACK, nack)
    time.sleep(1.0)
    final = api.get(f"/api/v1/commands/{command_id}", headers=bearer(dispatcher)).json
    assert (final["status"], final["nack_reason"]) == ("ACKED", None)


def test_an_unroutable_or_unreachable_broker_is_503_command_not_sent_and_failed(
    stack: Stack, make_user: Callable[[Role], User], password: str
) -> None:
    # A vhost with the command exchange but no queue: mandatory returns the message.
    vhost = f"empty-{secrets.token_hex(3)}"
    for args in (
        ["add_vhost", vhost],
        ["set_permissions", "-p", vhost, AMQP_USER, ".*", ".*", ".*"],
    ):
        code, output = stack.rabbit.exec(["rabbitmqctl", *args])
        assert code == 0, output
    connection = pika.BlockingConnection(stack.amqp(vhost))
    connection.channel().exchange_declare(COMMAND_EXCHANGE, "topic", durable=True)
    connection.close()

    dispatcher = make_user(Role.DISPATCHER)
    unreachable = stack.config(rabbitmq_port=1)
    for config in (stack.config(rabbitmq_vhost=vhost), unreachable):
        app = create_app(config)
        try:
            client = app.test_client()
            drone_id = f"f{secrets.token_hex(3)}-d001"
            with Session(create_engine(stack.database_url)) as session:
                session.add(Drone(id=drone_id, name="Test drone", home_lat=32.0, home_lon=34.8))
                session.commit()
                login = client.post(
                    "/api/v1/auth/login",
                    json={"email": dispatcher.email, "password": password},
                )
                token = login.json["access_token"]
                response = send(client, token, drone_id, {"type": "ARM"})
                assert response.status_code == 503
                assert response.json["error"] == "command_not_sent"
                rows = session.scalars(select(Command).where(Command.drone_id == drone_id)).all()
                assert [r.status.value for r in rows] == ["FAILED"]
        finally:
            app.extensions[PUBLISHER_KEY].stop()
