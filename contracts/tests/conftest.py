"""Shared fixtures for the contracts suite.

Tests run with ``--import-mode=importlib``, so they cannot import helpers from each other.
Everything shared is a fixture here, and ``valid_example`` / ``invalid_example`` are
parametrized over the golden files by ``pytest_generate_tests``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

CONTRACTS = Path(__file__).resolve().parents[1]
SCHEMAS = CONTRACTS / "schemas" / "v1"
EXAMPLES = CONTRACTS / "examples" / "v1"

# The catalogue as issue #6 and the design note list it. Tests compare the schemas to this.
COMMANDS = ("ARM", "TAKEOFF", "GOTO", "RETURN_HOME", "LAND", "SET_SPEED", "START_MISSION")
TELEMETRY = ("ACK", "NACK", "HEARTBEAT", "BATTERY", "POSITION", "MISSION_STATUS", "ALERT")


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(f"{name} is not JSON")


def _strict_json(text: str) -> Any:
    """json.loads that refuses NaN and Infinity, as RFC 8259 does."""
    return json.loads(text, parse_constant=_reject_constant)


def _payload_schema_path(type_: str) -> Path:
    group = "commands" if type_ in COMMANDS else "telemetry"
    return SCHEMAS / group / f"{type_.lower()}.schema.json"


@dataclass(frozen=True)
class Paths:
    contracts: Path
    schemas: Path
    examples: Path


@dataclass(frozen=True)
class SchemaSet:
    """Validates a whole message the way the schemas define it: envelope, then payload."""

    registry: Registry

    def validator(self, path: Path) -> Draft202012Validator:
        return Draft202012Validator({"$ref": path.as_uri()}, registry=self.registry)

    def envelope_errors(self, doc: Any) -> list[str]:
        envelope = self.validator(SCHEMAS / "envelope.schema.json")
        return [e.message for e in envelope.iter_errors(doc)]

    def payload_errors(self, type_: str, payload: Any) -> list[str]:
        validator = self.validator(_payload_schema_path(type_))
        return [e.message for e in validator.iter_errors(payload)]

    def errors(self, doc: Any) -> list[str]:
        problems = self.envelope_errors(doc)
        if problems:
            return problems
        if doc["type"] not in COMMANDS + TELEMETRY:
            return [f"unknown type {doc['type']}"]
        return self.payload_errors(doc["type"], doc["payload"])

    def errors_for_text(self, text: str) -> list[str]:
        """Errors for raw example text; text that is not strict JSON is one error."""
        try:
            doc = _strict_json(text)
        except ValueError as exc:
            return [f"not JSON: {exc}"]
        return self.errors(doc)


@pytest.fixture(scope="session")
def paths() -> Paths:
    return Paths(CONTRACTS, SCHEMAS, EXAMPLES)


@pytest.fixture(scope="session")
def schemas() -> SchemaSet:
    resources = [
        (p.as_uri(), Resource.from_contents(json.loads(p.read_text()), DRAFT202012))
        for p in SCHEMAS.rglob("*.schema.json")
    ]
    return SchemaSet(Registry().with_resources(resources))


@pytest.fixture(scope="session")
def catalogue() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(command types, telemetry types) as listed in issue #6."""
    return COMMANDS, TELEMETRY


@pytest.fixture(scope="session")
def strict_json() -> Callable[[str], Any]:
    return _strict_json


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    for kind in ("valid", "invalid"):
        name = f"{kind}_example"
        if name in metafunc.fixturenames:
            files = sorted(EXAMPLES.glob(f"*/{kind}/*.json"))
            ids = [f"{p.parent.parent.name}/{p.stem}" for p in files]
            metafunc.parametrize(name, files, ids=ids)
