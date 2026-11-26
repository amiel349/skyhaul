"""Golden examples of fleet API bodies (contracts/examples/api/<Schema>/{valid,invalid}/).

Every valid one must pass and every invalid one must fail against the component schema of
the OpenAPI contract. They pin the session shapes of ADR 0018 (login, refresh, errors).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

CONTRACTS = Path(__file__).resolve().parents[2]
SPEC: dict[str, Any] = json.loads((CONTRACTS / "openapi" / "fleet-api.v1.json").read_text())
EXAMPLES = CONTRACTS / "examples" / "api"
SPEC_URI = "urn:skyhaul:fleet-api:v1"
REGISTRY: Registry[Any] = Registry().with_resource(
    SPEC_URI, Resource.from_contents(SPEC, default_specification=DRAFT202012)
)


def _validator(schema_name: str) -> Draft202012Validator:
    assert schema_name in SPEC["components"]["schemas"], schema_name
    schema = {"$ref": f"{SPEC_URI}#/components/schemas/{schema_name}"}
    return Draft202012Validator(schema, registry=REGISTRY)


def _examples(kind: str) -> list[Path]:
    return sorted(EXAMPLES.glob(f"*/{kind}/*.json"))


@pytest.mark.parametrize("path", _examples("valid"), ids=lambda p: f"{p.parts[-3]}/{p.name}")
def test_valid_api_example_passes(path: Path) -> None:
    errors = list(_validator(path.parts[-3]).iter_errors(json.loads(path.read_text())))
    assert not errors, [e.message for e in errors[:3]]


@pytest.mark.parametrize("path", _examples("invalid"), ids=lambda p: f"{p.parts[-3]}/{p.name}")
def test_invalid_api_example_fails(path: Path) -> None:
    assert not _validator(path.parts[-3]).is_valid(json.loads(path.read_text()))


def test_every_api_example_folder_has_valid_and_invalid_examples() -> None:
    for folder in (p for p in EXAMPLES.iterdir() if p.is_dir()):
        for kind in ("valid", "invalid"):
            assert list((folder / kind).glob("*.json")), f"{folder.name}/{kind}"


def test_session_endpoints_send_the_csrf_header_and_take_the_cookie() -> None:
    for path in ("/auth/refresh", "/auth/logout"):
        op = SPEC["paths"][path]["post"]
        refs = {p["$ref"].rsplit("/", 1)[-1] for p in op["parameters"]}
        assert {"CsrfHeader", "RefreshCookie"} <= refs, path
        assert op["security"] == [], path  # the access token may have expired
    cookie = SPEC["components"]["parameters"]["RefreshCookie"]
    assert (cookie["in"], cookie["name"]) == ("cookie", "skyhaul_refresh")
    # The refresh token is never in a JSON body.
    for name in ("LoginResponse", "RefreshResponse"):
        assert "refresh_token" not in SPEC["components"]["schemas"][name]["properties"]
