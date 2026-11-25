"""An API app with unreachable dependencies (no test here talks to Postgres or Redis) and
two test-only routes: one that parses a JSON body and one that fails."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

import pytest
from flask import Flask, jsonify, request
from flask.testing import FlaskClient
from jsonschema import Draft202012Validator
from skyhaul_api import create_app
from skyhaul_api.auth.permissions import public
from skyhaul_api.commands.publisher import CommandPublisher
from skyhaul_api.config import Config

CONTRACT_PATH = Path(__file__).resolve().parents[3] / "contracts" / "openapi" / "fleet-api.v1.json"
FAULT_MARKER = "fault-detail-that-must-not-leak"


@pytest.fixture(scope="session")
def contract() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(CONTRACT_PATH.read_text())
    return loaded


@pytest.fixture(scope="session")
def error_validator(contract: dict[str, Any]) -> Draft202012Validator:
    """Validates a body against the contract's Error schema (with its $refs)."""
    # The contract's local $refs (#/components/...) resolve against this root.
    return Draft202012Validator(
        {**contract["components"]["schemas"]["Error"], "components": contract["components"]}
    )


@pytest.fixture(scope="session")
def contract_path() -> Path:
    return CONTRACT_PATH


@pytest.fixture(scope="session")
def fault_secret() -> str:
    """Text inside the test fault's exception; it must never reach a response."""
    return FAULT_MARKER


@pytest.fixture
def app() -> Flask:
    app = create_app(
        Config(
            database_url="postgresql+psycopg://nobody@127.0.0.1:1/none",
            redis_url="redis://127.0.0.1:1/0",
            jwt_secret=secrets.token_hex(32),
            rabbitmq_host="127.0.0.1",
            rabbitmq_user="nobody",
            rabbitmq_password=secrets.token_hex(8),
            rabbitmq_port=1,
        ),
        # Never started: not connected, and every publish resolves as STOPPED.
        publisher=CommandPublisher(lambda: None),
    )

    @app.post("/api/v1/_test/echo")
    @public
    def echo() -> Any:
        return jsonify(request.get_json())

    @app.get("/api/v1/_test/fault")
    @public
    def fault() -> Any:
        raise RuntimeError(FAULT_MARKER)

    return app


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()
