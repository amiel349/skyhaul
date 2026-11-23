"""Golden examples: every valid one passes and every invalid one fails, with the JSON Schema
validator and with the Codec (issue #6). Files live in contracts/examples/v1/<TYPE>/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from skyhaul_contracts import NACK_DETAIL_MAX_LENGTH, Codec, DecodeError, NackReason
from skyhaul_contracts.v1 import NackPayload


def test_valid_example_passes_jsonschema_and_codec(
    schemas: Any, strict_json: Any, valid_example: Path
) -> None:
    text = valid_example.read_text()

    assert schemas.errors_for_text(text) == []
    msg = Codec().decode(text.encode())
    assert msg.type == valid_example.parent.parent.name
    # Re-encoding gives back the same message; only unknown payload fields are dropped.
    again = strict_json(Codec().encode(msg).decode())
    original = strict_json(text)
    original["payload"].pop("added_in_v1_1", None)
    assert again == original


def test_invalid_example_fails_jsonschema_and_codec(invalid_example: Path, schemas: Any) -> None:
    text = invalid_example.read_text()

    assert schemas.errors_for_text(text), "the JSON Schema validator accepted it"
    with pytest.raises(DecodeError):
        Codec().decode(text.encode())


def test_every_type_has_valid_and_invalid_examples(paths: Any, catalogue: Any) -> None:
    for type_ in catalogue[0] + catalogue[1]:
        for kind in ("valid", "invalid"):
            assert list((paths.examples / type_ / kind).glob("*.json")), f"{type_}/{kind}"


def test_examples_are_named_by_type_folder(paths: Any, catalogue: Any) -> None:
    folders = {p.name for p in paths.examples.iterdir() if p.is_dir()}
    assert folders == set(catalogue[0] + catalogue[1])
    for path in paths.examples.glob("*/valid/*.json"):
        assert json.loads(path.read_text())["type"] == path.parent.parent.name, path


def test_every_decode_error_gives_a_detail_a_nack_can_carry(
    invalid_example: Path, paths: Any
) -> None:
    with pytest.raises(DecodeError) as exc:
        Codec().decode(invalid_example.read_bytes())

    nack = NackPayload(
        command_msg_id="7a1c3e5f-2b4d-4c6e-8f10-000000000001",
        command_type="",
        reason=NackReason.INVALID_PAYLOAD,
        detail=exc.value.detail,
    )
    assert len(nack.detail) <= NACK_DETAIL_MAX_LENGTH


def test_nack_detail_limit_matches_the_schema(paths: Any) -> None:
    nack = json.loads((paths.schemas / "telemetry" / "nack.schema.json").read_text())
    assert nack["properties"]["detail"]["maxLength"] == NACK_DETAIL_MAX_LENGTH
