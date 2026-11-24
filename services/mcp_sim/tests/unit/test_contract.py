"""Contract: the control tools' input schemas match the control API's OpenAPI spec
(names, types and ranges). Nullability is not compared: the tools use null for "absent"."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from mcp_support.harness import new_harness

SPEC = yaml.safe_load(
    (Path(__file__).resolve().parents[3] / "drone_sim" / "openapi.yaml").read_text()
)
SCHEMAS = SPEC["components"]["schemas"]
KEYWORDS = ("type", "const", "minimum", "maximum", "exclusiveMinimum", "minLength", "maxLength")


def resolve(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Follow $ref and drop a `null` branch of anyOf/oneOf."""
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return resolve(defs[name], defs)
    for key in ("anyOf", "oneOf"):
        branches = [b for b in schema.get(key, []) if b.get("type") != "null"]
        if len(branches) == 1:
            return resolve(branches[0], defs)
    return schema


def shape(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    schema = resolve(schema, defs)
    out = {k: schema[k] for k in KEYWORDS if k in schema}
    if "pattern" in schema:
        out["pattern"] = schema["pattern"].strip("^$")
    return out


@pytest.fixture(scope="module")
def tools() -> dict[str, dict[str, Any]]:
    return {t.name: t.inputSchema for t in new_harness().list_tools()}


def tool_field(tools: dict[str, Any], tool: str, name: str) -> dict[str, Any]:
    schema = tools[tool]
    return shape(schema["properties"][name], schema.get("$defs", {}))


def api(schema: dict[str, Any]) -> dict[str, Any]:
    return shape(schema, SCHEMAS)


def test_spawn_drones_matches_spawn_request(tools: dict[str, Any]) -> None:
    request = SCHEMAS["SpawnRequest"]["properties"]
    home = resolve(request["home"], SCHEMAS)["properties"]

    assert tool_field(tools, "spawn_drones", "namespace") == api(request["namespace"])
    assert tool_field(tools, "spawn_drones", "count") == api(request["count"])
    assert tool_field(tools, "spawn_drones", "home_lat") == api(home["lat"])
    assert tool_field(tools, "spawn_drones", "home_lon") == api(home["lon"])
    assert tool_field(tools, "spawn_drones", "battery_pct") == api(request["battery_pct"])


def test_namespace_and_drone_id_arguments_match_the_spec(tools: dict[str, Any]) -> None:
    list_param = SPEC["paths"]["/drones"]["get"]["parameters"][0]["schema"]
    reset = SCHEMAS["ResetRequest"]["properties"]["namespace"]
    drone_param = api(SPEC["paths"]["/drones/{drone_id}"]["parameters"][0]["schema"])

    assert tool_field(tools, "list_drones", "namespace") == api(list_param)
    assert tool_field(tools, "reset_namespace", "namespace") == api(reset)
    assert tools["reset_namespace"]["required"] == ["namespace"]
    for tool in ("get_drone", "clear_faults", "inject_fault"):
        drone_id = tool_field(tools, tool, "drone_id")
        # The tool adds the contracts' DroneId pattern on top of the spec's length bounds.
        assert {k: v for k, v in drone_id.items() if k != "pattern"} == drone_param


def test_inject_fault_has_one_shape_per_fault_request_kind(tools: dict[str, Any]) -> None:
    schema = tools["inject_fault"]
    defs = schema["$defs"]
    union = schema["properties"]["fault"]
    tool_kinds = {
        resolve(b, defs)["properties"]["kind"]["const"]: resolve(b, defs) for b in union["oneOf"]
    }
    api_kinds = {b["properties"]["kind"]["const"]: b for b in SCHEMAS["FaultRequest"]["oneOf"]}

    assert set(tool_kinds) == set(api_kinds)
    for kind, expected in api_kinds.items():
        actual = tool_kinds[kind]
        assert set(actual["properties"]) == set(expected["properties"]), kind
        assert set(actual["required"]) == set(expected["required"]), kind
        assert actual["additionalProperties"] is False
        for name, prop in expected["properties"].items():
            if name != "kind":
                assert shape(actual["properties"][name], defs) == api(prop), (kind, name)
