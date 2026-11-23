"""The generated models must match the schemas (issue #6, ADR 0005).

The full check (Python and TypeScript) runs in `make check` and the CI `contracts` job.
These tests run the Python half, which needs no Node, so they also run in the CI python job.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# Each test runs the generator in a subprocess; allow a slow CI runner.
pytestmark = pytest.mark.timeout(150)

GENERATE = Path(__file__).resolve().parents[2] / "scripts" / "generate.py"


def _check(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, str(GENERATE), "--check", "--skip-ts", "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _copy_contracts(paths: Any, tmp_path: Path) -> Path:
    shutil.copytree(
        paths.contracts,
        tmp_path / "contracts",
        ignore=shutil.ignore_patterns("__pycache__", "tests", "examples", "ts"),
    )
    return tmp_path


def test_committed_python_models_match_schemas(paths: Any) -> None:
    result = _check(paths.contracts.parent)
    assert result.returncode == 0, result.stderr


def test_schema_change_without_regeneration_fails_with_run_make_contracts(
    paths: Any, tmp_path: Path
) -> None:
    root = _copy_contracts(paths, tmp_path)
    schema_path = root / "contracts" / "schemas" / "v1" / "commands" / "takeoff.schema.json"
    schema = json.loads(schema_path.read_text())
    schema["properties"]["climb_rate_mps"] = {"type": "number", "minimum": 0, "maximum": 5}
    schema_path.write_text(json.dumps(schema, indent=2))

    result = _check(root)

    assert result.returncode == 1
    assert "run make contracts" in result.stderr
    assert "takeoff_schema.py (changed)" in result.stderr


def test_hand_edited_generated_file_fails_the_check(paths: Any, tmp_path: Path) -> None:
    root = _copy_contracts(paths, tmp_path)
    generated = root / "contracts" / "src" / "skyhaul_contracts" / "v1" / "_generated"
    (generated / "extra.py").write_text("# not generated\n")

    result = _check(root)

    assert result.returncode == 1
    assert "extra.py (not generated)" in result.stderr
