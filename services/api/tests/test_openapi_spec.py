"""``flask spec`` exports valid OpenAPI that matches the committed contract, and the check
behind ``make openapi-check`` fails when they differ (#24)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from skyhaul_api import create_spec_app


def _spec(*args: str) -> tuple[int, str]:
    result = create_spec_app().test_cli_runner().invoke(args=["spec", *args])
    return result.exit_code, result.output


def test_spec_is_valid_matches_the_contract_and_the_check_fails_on_a_difference(
    tmp_path: Path, contract: dict[str, Any], contract_path: Path
) -> None:
    code, output = _spec("--contract", str(contract_path))
    assert code == 0, output
    exported = json.loads(output)
    meta = json.loads(
        (contract_path.parent / "meta" / "oas-3.1-schema-2022-10-07.json").read_text()
    )
    assert not list(Draft202012Validator(meta).iter_errors(exported))
    assert {"/healthz", "/readyz", "/auth/login", "/drones"} <= set(exported["paths"])

    code, output = _spec("--check", "--contract", str(contract_path))
    assert code == 0, output

    changed = json.loads(json.dumps(contract))
    changed["paths"]["/healthz"]["get"]["summary"] = "Changed in the contract only"
    changed_path = tmp_path / "changed.json"
    changed_path.write_text(json.dumps(changed))
    code, output = _spec("--check", "--contract", str(changed_path))
    assert code == 1
    assert "paths./healthz.get.summary: differs" in output
