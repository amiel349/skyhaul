"""Shared fixtures for the contracts suite.

Tests run with ``--import-mode=importlib``, so they cannot import helpers from each other.
Everything shared is a fixture here, and ``valid_example`` / ``invalid_example`` are
parametrized over the golden files by ``pytest_generate_tests``.
"""

from __future__ import annotations

import functools
import json
import math
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import pytest
from jsonschema import Draft202012Validator, ValidationError, validators
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


def _finite(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("number overflows to infinity")
    return value


def _lone_surrogate(obj: Any) -> bool:
    stack = [obj]
    while stack:
        item = stack.pop()
        if isinstance(item, str) and any("\ud800" <= c <= "\udfff" for c in item):
            return True
        if isinstance(item, dict):
            stack += [*item.keys(), *item.values()]
        elif isinstance(item, list):
            stack += item
    return False


def _strict_json(text: str) -> Any:
    """The wire's JSON rules, written independently of the Codec: RFC 8259 without NaN or
    Infinity, no number that overflows to infinity, no lone surrogates (I-JSON, RFC 7493).
    Raises ValueError."""
    try:
        obj = json.loads(text, parse_constant=_reject_constant, parse_float=_finite)
    except RecursionError:
        raise ValueError("nested too deep") from None
    if _lone_surrogate(obj):
        raise ValueError("lone surrogate")
    return obj


@functools.cache
def _ecma_regex(pattern: str) -> re.Pattern[str]:
    """JSON Schema patterns are ECMA-262: `$` (no m flag) matches only at the very end.
    Python's `$` also matches before a final newline, so "gw0-d001\n" would pass. Our
    patterns only use `$` as the final anchor; translate it to `\\Z`."""
    if pattern.endswith("$") and not pattern.endswith("\\$"):
        pattern = pattern[:-1] + r"\Z"
    return re.compile(pattern)


def _ecma_pattern(
    validator: Any, pattern: str, instance: Any, schema: dict[str, Any]
) -> Iterator[ValidationError]:
    if validator.is_type(instance, "string") and not _ecma_regex(pattern).search(instance):
        yield ValidationError(f"{instance!r} does not match {pattern!r}")


# Draft 2020-12 with ECMA-262 `pattern` semantics; every other keyword is the stock one.
EcmaValidator = validators.extend(Draft202012Validator, {"pattern": _ecma_pattern})


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

    def validator(self, path: Path) -> Any:
        return EcmaValidator({"$ref": path.as_uri()}, registry=self.registry)

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
    def load(path: Path) -> Resource[Any]:
        contents = json.loads(path.read_text())
        # jsonschema switches to the stock validator class for a referenced resource that
        # declares $schema, which would drop the ECMA pattern keyword after every $ref. All
        # files are draft 2020-12 (test_every_schema_file_is_valid_draft_2020_12), so the
        # registry copy can leave the declaration out.
        contents.pop("$schema", None)
        return Resource.from_contents(contents, DRAFT202012)

    resources = [(p.as_uri(), load(p)) for p in SCHEMAS.rglob("*.schema.json")]
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
