"""The MCP server in-process, driven by the SDK's client session, against the simulator as a
subprocess and a Testcontainers RabbitMQ (#11 integration criteria). Needs Docker."""

from __future__ import annotations

import json
import time
from typing import Any

import anyio
import pika
import pytest
from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult
from skyhaul_contracts.topology import SIM_COMMAND_QUEUE
from skyhaul_mcp_sim.config import Config
from skyhaul_mcp_sim.server import build_server

from .conftest import Broker, Sim

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]

ALTITUDE_M = 20.0
TARGET_LAT, TARGET_LON = 32.001, 34.8  # about 110 m north of the default home


async def call(session: ClientSession, tool: str, **arguments: Any) -> Any:
    result: CallToolResult = await session.call_tool(tool, arguments)
    text = result.content[0].text  # type: ignore[union-attr]
    assert not result.isError, text
    return json.loads(text)


async def wait_state(session: ClientSession, drone: str, state: str) -> None:
    deadline = time.monotonic() + 30
    while (await call(session, "get_drone", drone_id=drone))["state"] != state:
        assert time.monotonic() < deadline, f"{drone} never reached {state}"
        await anyio.sleep(0.2)


def test_spawn_then_commands_get_matching_replies_and_telemetry_is_bounded(sim: Sim) -> None:
    server = build_server(Config.from_env(sim.env))

    async def scenario() -> None:
        async with create_connected_server_and_client_session(server) as session:
            spawned = await call(session, "spawn_drones", count=1)
            drone = spawned["drones"][0]["drone_id"]
            assert drone == "dev-d001"

            arm = await call(session, "send_command", drone_id=drone, type="ARM")
            takeoff = await call(
                session,
                "send_command",
                drone_id=drone,
                type="TAKEOFF",
                payload={"altitude_m": ALTITUDE_M},
            )
            await wait_state(session, drone, "HOVERING")
            goto = await call(
                session,
                "send_command",
                drone_id=drone,
                type="GOTO",
                payload={"lat": TARGET_LAT, "lon": TARGET_LON, "altitude_m": ALTITUDE_M},
            )
            for reply in (arm, takeoff, goto):
                assert reply["result"] == "ACK"
                assert reply["reply"]["payload"]["command_msg_id"] == reply["command"]["msg_id"]
            assert len({r["command"]["msg_id"] for r in (arm, takeoff, goto)}) == 3

            beats = await call(
                session, "read_telemetry", drone_id=drone, types=["HEARTBEAT"], seconds=3, max=2
            )
            assert beats["count"] == 2
            assert {m["type"] for m in beats["messages"]} == {"HEARTBEAT"}
            assert {m["drone_id"] for m in beats["messages"]} == {drone}

            await call(session, "reset_namespace", namespace="dev")

    anyio.run(scenario)


def test_send_command_without_the_simulator_queue_is_unroutable_at_once(
    broker: Broker,
) -> None:
    connection = pika.BlockingConnection(broker.params)
    connection.channel().queue_delete(SIM_COMMAND_QUEUE)  # as on a broker the sim never used
    connection.close()
    env = {
        "RABBITMQ_HOST": broker.host,
        "RABBITMQ_PORT": str(broker.port),
        "RABBITMQ_USER": broker.user,
        "RABBITMQ_PASSWORD": broker.password,
    }
    server = build_server(Config.from_env(env))

    async def scenario() -> tuple[CallToolResult, float]:
        async with create_connected_server_and_client_session(server) as session:
            started = time.monotonic()
            result = await session.call_tool(
                "send_command", {"drone_id": "dev-d001", "type": "ARM", "timeout_s": 10}
            )
            return result, time.monotonic() - started

    result, elapsed = anyio.run(scenario)

    assert result.isError
    assert json.loads(result.content[0].text)["error"] == "unroutable"  # type: ignore[union-attr]
    assert elapsed < 2.0
