"""The fleet API contract (contracts/openapi/fleet-api.v1.json) is valid OpenAPI 3.1 (#23).

The OpenAPI 3.1 meta-schema only checks the document's structure, so the component schemas
are also checked as JSON Schema 2020-12, every local $ref must resolve, and the enums shared
with the drone messages must equal the message contracts.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

OPENAPI = Path(__file__).resolve().parents[2] / "openapi"
SPEC: dict[str, Any] = json.loads((OPENAPI / "fleet-api.v1.json").read_text())
COMMON: dict[str, Any] = json.loads(
    (OPENAPI.parent / "schemas" / "v1" / "common.schema.json").read_text()
)["$defs"]


def _refs(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from _refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _refs(value)


def test_document_is_valid_against_the_openapi_3_1_meta_schema() -> None:
    meta = json.loads((OPENAPI / "meta" / "oas-3.1-schema-2022-10-07.json").read_text())
    errors = sorted(Draft202012Validator(meta).iter_errors(SPEC), key=str)
    assert not errors, [f"{list(e.absolute_path)}: {e.message}" for e in errors[:5]]
    assert SPEC["openapi"].startswith("3.1.")


@pytest.mark.parametrize("name", sorted(SPEC["components"]["schemas"]))
def test_component_schema_is_valid_json_schema(name: str) -> None:
    Draft202012Validator.check_schema(SPEC["components"]["schemas"][name])


def test_every_local_ref_resolves() -> None:
    for ref in set(_refs(SPEC)):
        assert ref.startswith("#/"), ref  # self-contained: no external files
        node: Any = SPEC
        for part in ref[2:].split("/"):
            assert part in node, ref
            node = node[part]


def test_every_operation_is_authenticated_or_explicitly_public() -> None:
    assert SPEC["security"] == [{"bearerAuth": []}]
    for path, item in SPEC["paths"].items():
        for method, op in item.items():
            if method == "parameters":
                continue
            assert op.get("security") == [] or op.get("x-permission"), f"{method} {path}"
            assert "200" in op["responses"] or any(c[0] == "2" for c in op["responses"])


@pytest.mark.parametrize(
    ("api_name", "common_name"),
    [
        ("Uuid", "Uuid"),
        ("DroneId", "DroneId"),
        ("FlightState", "FlightState"),
        ("CommandType", "CommandType"),
        ("NackReason", "NackReason"),
        ("AlertSeverity", "AlertSeverity"),
    ],
)
def test_shared_definitions_equal_the_message_contracts(api_name: str, common_name: str) -> None:
    api, common = SPEC["components"]["schemas"][api_name], COMMON[common_name]
    assert api.get("enum") == common.get("enum")
    assert api.get("pattern") == common.get("pattern")


def test_backend_alert_codes_are_the_contract_codes_plus_link_lost() -> None:
    codes = SPEC["components"]["schemas"]["FleetAlertCode"]["enum"]
    assert codes == [*COMMON["AlertCode"]["enum"], "LINK_LOST"]


def test_raw_command_endpoint_excludes_start_mission() -> None:
    direct = SPEC["components"]["schemas"]["DirectCommandType"]["enum"]
    assert direct == [t for t in COMMON["CommandType"]["enum"] if t != "START_MISSION"]


def test_socket_events_are_named_component_schemas() -> None:
    events = SPEC["x-socketio"]["server_events"]
    assert set(events) == {
        "drone.state",
        "command.updated",
        "mission.updated",
        "alert.raised",
        "alert.updated",
    }
    assert SPEC["x-socketio"]["client_events"] == {}
