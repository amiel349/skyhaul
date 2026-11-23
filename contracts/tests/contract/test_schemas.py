"""Contract tests over the schema files themselves (issue #6)."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from skyhaul_contracts import Codec, MalformedMessage
from skyhaul_contracts import types as contract_types
from skyhaul_contracts.topology import DRONE_ID_RE, TYPE_NAME_RE

COMMANDS_AND_TELEMETRY = (
    "ARM", "TAKEOFF", "GOTO", "RETURN_HOME", "LAND", "SET_SPEED", "START_MISSION",
    "ACK", "NACK", "HEARTBEAT", "BATTERY", "POSITION", "MISSION_STATUS", "ALERT",
)  # fmt: skip
ENVELOPE_FIELDS = (
    "msg_id",
    "correlation_id",
    "drone_id",
    "type",
    "schema_version",
    "ts",
    "payload",
)

# Values fixed by the M1 design note (docs/architecture/m1-design.md, section 2).
DESIGN_ENUMS = {
    "FlightState": [
        "LANDED", "ARMED", "TAKING_OFF", "HOVERING", "FLYING", "RETURNING", "LANDING",
    ],
    "NackReason": [
        "UNKNOWN_TYPE", "UNSUPPORTED_VERSION", "INVALID_PAYLOAD", "INVALID_STATE",
        "MISSION_ACTIVE", "BATTERY_TOO_LOW", "OUT_OF_BOUNDS", "INJECTED", "INTERNAL_ERROR",
    ],
    "AlertCode": ["LOW_BATTERY", "CRITICAL_BATTERY", "GEOFENCE_BREACH", "LINK_RESTORED"],
}  # fmt: skip


def _valid_arm() -> dict[str, Any]:
    return {
        "msg_id": "7a1c3e5f-2b4d-4c6e-8f10-000000000001",
        "correlation_id": None,
        "drone_id": "gw0-d001",
        "type": "ARM",
        "schema_version": 1,
        "ts": "2026-11-23T10:00:00.123Z",
        "payload": {},
    }


def _common(paths: Any) -> dict[str, Any]:
    return json.loads((paths.schemas / "common.schema.json").read_text())


def test_every_schema_file_is_valid_draft_2020_12(paths: Any) -> None:
    files = sorted(paths.schemas.rglob("*.schema.json"))
    assert len(files) == 16  # envelope, common, 7 commands, 7 telemetry
    for path in files:
        schema = json.loads(path.read_text())
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema", path.name
        Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("field", ENVELOPE_FIELDS)
def test_envelope_missing_field_fails_and_names_the_field(schemas: Any, field: str) -> None:
    doc = _valid_arm()
    del doc[field]

    errors = schemas.envelope_errors(doc)
    assert errors == [f"'{field}' is a required property"]

    with pytest.raises(MalformedMessage) as exc:
        Codec().decode(json.dumps(doc).encode())
    assert exc.value.errors == (f"{field}: missing",)


def test_envelope_extra_field_fails_envelope_is_closed(schemas: Any) -> None:
    doc = _valid_arm() | {"priority": "high"}

    assert schemas.envelope_errors(doc), "extra envelope field must fail"
    with pytest.raises(MalformedMessage) as exc:
        Codec().decode(json.dumps(doc).encode())
    assert exc.value.errors == ("<extra>: extra_forbidden",)


@pytest.mark.parametrize("type_", [*COMMANDS_AND_TELEMETRY])
def test_payload_extra_unknown_field_passes_and_is_ignored(
    paths: Any, schemas: Any, type_: str
) -> None:
    doc = json.loads((paths.examples / type_ / "valid" / "minimal.json").read_text())
    doc["payload"]["added_in_a_later_version"] = {"nested": [1, 2]}

    assert schemas.errors(doc) == []
    msg = Codec().decode(json.dumps(doc).encode())
    assert "added_in_a_later_version" not in msg.payload.model_dump()


def test_payload_schemas_leave_additional_properties_open(paths: Any) -> None:
    for path in sorted(paths.schemas.rglob("*.schema.json")):
        if path.name == "envelope.schema.json":
            continue
        for node in _object_nodes(json.loads(path.read_text())):
            assert node.get("additionalProperties", True) is True, path.name


def _object_nodes(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            found.append(node)
        for value in node.values():
            found += _object_nodes(value)
    elif isinstance(node, list):
        for value in node:
            found += _object_nodes(value)
    return found


def test_one_schema_per_command_and_per_telemetry_type(paths: Any, catalogue: Any) -> None:
    commands, telemetry = catalogue

    def listed(group: str) -> set[str]:
        files = (paths.schemas / group).glob("*.schema.json")
        return {p.name.removesuffix(".schema.json").upper() for p in files}

    assert listed("commands") == set(commands)
    assert listed("telemetry") == set(telemetry)

    common = _common(paths)["$defs"]
    assert common["CommandType"]["enum"] == list(commands)
    assert common["TelemetryType"]["enum"] == list(telemetry)
    assert common["MessageType"]["enum"] == list(commands) + list(telemetry)
    assert {(1, t) for t in contract_types.MessageType} == set(contract_types.PAYLOADS)


@pytest.mark.parametrize("enum_name", sorted(DESIGN_ENUMS))
def test_enums_hold_design_values_and_python_enums_match_schema(paths: Any, enum_name: str) -> None:
    schema_values = _common(paths)["$defs"][enum_name]["enum"]
    python_values = [m.value for m in getattr(contract_types, enum_name)]

    assert schema_values == DESIGN_ENUMS[enum_name]
    assert python_values == schema_values


@pytest.mark.parametrize(
    "enum_name",
    ["MessageType", "CommandType", "TelemetryType", "AlertSeverity", "MissionState"],
)
def test_other_python_enums_match_schema(paths: Any, enum_name: str) -> None:
    schema_values = _common(paths)["$defs"][enum_name]["enum"]
    assert [m.value for m in getattr(contract_types, enum_name)] == schema_values


@pytest.mark.parametrize(
    ("def_name", "regex"), [("DroneId", DRONE_ID_RE), ("TypeName", TYPE_NAME_RE)]
)
def test_routing_key_word_rules_match_schema_patterns(
    paths: Any, def_name: str, regex: re.Pattern[str]
) -> None:
    pattern = _common(paths)["$defs"][def_name]["pattern"]
    assert pattern == f"^{regex.pattern}$"


def test_every_numeric_field_has_bounds(paths: Any) -> None:
    def numeric_nodes(node: Any, where: str) -> list[tuple[str, dict[str, Any]]]:
        out: list[tuple[str, dict[str, Any]]] = []
        if isinstance(node, dict):
            declared = node.get("type")  # a property may itself be called "type"
            types = declared if isinstance(declared, list) else [declared]
            if {"number", "integer"} & {t for t in types if isinstance(t, str)}:
                out.append((where, node))
            for key, value in node.items():
                out += numeric_nodes(value, f"{where}/{key}")
        return out

    for path in sorted(paths.schemas.rglob("*.schema.json")):
        for where, node in numeric_nodes(json.loads(path.read_text()), path.name):
            if "const" in node:
                continue
            assert {"minimum", "exclusiveMinimum"} & node.keys(), where
            assert {"maximum", "exclusiveMaximum"} & node.keys(), where


def test_optional_payload_fields_are_never_nullable(paths: Any) -> None:
    """Absent and null must not both be legal: the Codec omits unset optional fields."""

    def allows_null(prop: dict[str, Any]) -> bool:
        declared = prop.get("type")
        types = declared if isinstance(declared, list) else [declared]
        return "null" in types or any(allows_null(p) for p in prop.get("anyOf", []))

    for path in sorted(paths.schemas.rglob("*.schema.json")):
        for node in _object_nodes(json.loads(path.read_text())):
            required = set(node.get("required", []))
            for name, prop in node.get("properties", {}).items():
                if name not in required:
                    assert not allows_null(prop), f"{path.name}: {name}"
