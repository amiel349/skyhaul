"""Unit-test support: a mocked control API (httpx.MockTransport), a fake broker, and a
helper that calls tools through a real MCP client session connected in memory."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import anyio
import httpx
import pika
import pika.exceptions
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult, Tool
from skyhaul_contracts import Codec, MessageType
from skyhaul_contracts.v1 import AckPayload, NackPayload
from skyhaul_mcp_sim.config import Config
from skyhaul_mcp_sim.control import ControlApi
from skyhaul_mcp_sim.ground import GroundStation
from skyhaul_mcp_sim.server import build_server

TOKEN = "tok-" + "7" * 40
PASSWORD = "pw-" + "9" * 30
ENV = {
    "SIM_CONTROL_URL": "http://sim.test",
    "SIM_CONTROL_TOKEN": TOKEN,
    "RABBITMQ_USER": "ground",
    "RABBITMQ_PASSWORD": PASSWORD,
}

type Handler = Callable[[httpx.Request], httpx.Response]


@dataclass
class ControlMock:
    """Answers every request with `handler`; remembers the requests."""

    handler: Handler = lambda request: httpx.Response(200, json={})
    requests: list[httpx.Request] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self.handler(request)

        return httpx.MockTransport(handle)

    def body(self, index: int = -1) -> Any:
        return json.loads(self.requests[index].content)


# -- fake broker ----------------------------------------------------------------------------


@dataclass
class Published:
    exchange: str
    routing_key: str
    body: bytes
    mandatory: bool


type Responder = Callable[[bytes], list[bytes]]


@dataclass
class FakeBroker:
    """One fake broker for all connections. `respond` turns a published command into the
    bodies delivered to the tap queue; `telemetry` is delivered to every new tap."""

    published: list[Published] = field(default_factory=list)
    telemetry: list[bytes] = field(default_factory=list)
    respond: Responder = lambda body: []
    unroutable: bool = False
    down: bool = False
    acks: list[int] = field(default_factory=list)
    nacks: list[int] = field(default_factory=list)
    bindings: list[str] = field(default_factory=list)

    def connect(self, params: pika.ConnectionParameters) -> FakeConnection:
        if self.down:
            raise pika.exceptions.AMQPConnectionError("connection refused")
        return FakeConnection(self)


class FakeConnection:
    def __init__(self, broker: FakeBroker) -> None:
        self.broker = broker
        self.is_open = True
        self.pending: list[bytes] = list(broker.telemetry)
        self.callback: Callable[..., None] | None = None
        self._tag = 0

    def channel(self) -> FakeChannel:
        return FakeChannel(self)

    def process_data_events(self, time_limit: float = 0) -> None:
        if not self.pending:
            time.sleep(min(time_limit, 0.01))
            return
        while self.pending and self.callback is not None:
            self._tag += 1
            body = self.pending.pop(0)
            self.callback(self.channel(), SimpleNamespace(delivery_tag=self._tag), None, body)

    def close(self) -> None:
        self.is_open = False


class FakeChannel:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.broker = connection.broker

    def confirm_delivery(self) -> None: ...
    def exchange_declare(self, *args: Any, **kwargs: Any) -> None: ...

    def queue_declare(self, queue: str, **kwargs: Any) -> Any:
        return SimpleNamespace(method=SimpleNamespace(queue="amq.gen-test"))

    def queue_bind(self, queue: str, exchange: str, routing_key: str) -> None:
        self.broker.bindings.append(routing_key)

    def basic_consume(self, queue: str, on_message_callback: Callable[..., None]) -> None:
        self.connection.callback = on_message_callback

    def basic_publish(
        self, exchange: str, routing_key: str, body: bytes, properties: Any, mandatory: bool
    ) -> None:
        if self.broker.unroutable:
            raise pika.exceptions.UnroutableError([])
        self.broker.published.append(Published(exchange, routing_key, body, mandatory))
        self.connection.pending.extend(self.broker.respond(body))

    def basic_ack(self, delivery_tag: int) -> None:
        self.broker.acks.append(delivery_tag)

    def basic_nack(self, delivery_tag: int, requeue: bool = True) -> None:
        self.broker.nacks.append(delivery_tag)


def reply_to(kind: MessageType, **nack: Any) -> Responder:
    """A drone that answers every command with an ACK (or a NACK with `nack` fields)."""
    codec = Codec()

    def respond(body: bytes) -> list[bytes]:
        command = codec.decode(body)
        if kind is MessageType.ACK:
            payload: Any = AckPayload(command_msg_id=command.msg_id, command_type=command.type)
        else:
            payload = NackPayload(
                command_msg_id=command.msg_id, command_type=command.type.value, **nack
            )
        reply = codec.new(kind, command.drone_id, payload, command.correlation_id)
        return [codec.encode(reply)]

    return respond


# -- server --------------------------------------------------------------------------------


@dataclass
class Harness:
    control: ControlMock
    broker: FakeBroker
    env: dict[str, str]

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
        return self.calls((name, arguments or {}))[0]

    def calls(self, *calls: tuple[str, dict[str, Any]]) -> list[CallToolResult]:
        """Several tool calls, in order, in one client session against one server."""
        config = Config.from_env(self.env)
        server = build_server(
            config,
            ControlApi(config, transport=self.control.transport()),
            GroundStation(config.broker_params, connect=self.broker.connect),
        )

        async def go() -> list[CallToolResult]:
            async with create_connected_server_and_client_session(server) as session:
                return [await session.call_tool(name, args) for name, args in calls]

        return anyio.run(go)

    def list_tools(self) -> list[Tool]:
        server = build_server(Config.from_env(self.env))

        async def go() -> list[Tool]:
            async with create_connected_server_and_client_session(server) as session:
                return (await session.list_tools()).tools

        return anyio.run(go)


def new_harness() -> Harness:
    return Harness(ControlMock(), FakeBroker(), dict(ENV))


def data(result: CallToolResult) -> Any:
    """The JSON text of a result; it must equal the structured content."""
    text = result.content[0].text  # type: ignore[union-attr]
    parsed = json.loads(text)
    assert parsed == result.structuredContent
    return parsed
