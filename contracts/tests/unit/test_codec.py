"""Codec unit tests (issue #6). Test names follow the acceptance criteria."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pika
import pytest
from pydantic import BaseModel, ValidationError
from skyhaul_contracts import (
    MAX_MESSAGE_BYTES,
    Codec,
    CommandType,
    DecodeError,
    EncodeError,
    InvalidPayload,
    MalformedMessage,
    Message,
    MessageType,
    UnknownType,
    UnsupportedVersion,
)
from skyhaul_contracts.v1 import (
    AckPayload,
    ArmPayload,
    GeoPoint,
    GotoPayload,
    PositionPayload,
    TakeoffPayload,
)

DRONE = "gw0-d001"
CMD_ID = "0b0f4c6e-1f7a-4b8e-9c55-3d1e2f6a7b01"
CORR = "5c2d7e9a-8b31-4f60-a1d4-6e7f8091a2b3"


def _doc(type_: str = "ARM", payload: Any = None, **envelope: Any) -> dict[str, Any]:
    doc = {
        "msg_id": "7a1c3e5f-2b4d-4c6e-8f10-000000000001",
        "correlation_id": CORR,
        "drone_id": DRONE,
        "type": type_,
        "schema_version": 1,
        "ts": "2026-11-23T10:00:00.123Z",
        "payload": {} if payload is None else payload,
    }
    doc.update(envelope)
    return doc


def _bytes(doc: Any) -> bytes:
    return json.dumps(doc).encode()


def _position(**changes: Any) -> dict[str, Any]:
    payload = {
        "lat": 52.2297,
        "lon": 21.0122,
        "altitude_m": 40,
        "heading_deg": 90,
        "ground_speed_mps": 10,
        "target": None,
    }
    return payload | changes


# -- round trip ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "type_",
    [
        "ARM", "TAKEOFF", "GOTO", "RETURN_HOME", "LAND", "SET_SPEED", "START_MISSION",
        "ACK", "NACK", "HEARTBEAT", "BATTERY", "POSITION", "MISSION_STATUS", "ALERT",
    ],
)  # fmt: skip
def test_decode_then_encode_returns_the_input_message(paths: Any, type_: str) -> None:
    codec = Codec()
    body = (paths.examples / type_ / "valid" / "minimal.json").read_bytes()

    again = codec.encode(codec.decode(body))

    assert json.loads(again) == json.loads(body)
    # Codec output is canonical: a second round trip is byte-identical.
    assert codec.encode(codec.decode(again)) == again


def test_new_message_round_trips_and_equals_itself() -> None:
    codec = Codec()
    goto = GotoPayload(lat=52.2, lon=21.0, altitude_m=40, speed_mps=12.5)
    msg = codec.new(MessageType.GOTO, DRONE, goto, CORR)

    decoded = codec.decode(codec.encode(msg))

    assert decoded == msg
    assert decoded.payload == goto


def test_new_fills_fresh_msg_id_ts_and_version() -> None:
    codec = Codec()
    when = datetime(2026, 11, 23, 12, 0, 0, 123456, tzinfo=timezone(timedelta(hours=2)))
    first = codec.new(MessageType.ARM, DRONE, ArmPayload(), None, ts=when)
    second = codec.new(MessageType.ARM, DRONE, ArmPayload(), None)

    assert first.msg_id != second.msg_id
    assert first.ts == "2026-11-23T10:00:00.123Z"
    assert first.schema_version == 1
    assert first.correlation_id is None


# -- tolerant payloads --------------------------------------------------------------------


def test_payload_extra_unknown_field_is_ignored() -> None:
    msg = Codec().decode(_bytes(_doc("TAKEOFF", {"altitude_m": 30, "climb_rate_mps": 2})))

    assert msg.payload == TakeoffPayload(altitude_m=30)
    assert "climb_rate_mps" not in msg.payload.model_dump()


# -- typed decode errors ------------------------------------------------------------------


@pytest.mark.parametrize("version", [0, 2, 99, -1])
def test_unsupported_version_raises_with_msg_id_drone_id_type_correlation_id(
    version: int,
) -> None:
    with pytest.raises(UnsupportedVersion) as exc:
        Codec().decode(_bytes(_doc("TAKEOFF", schema_version=version, payload={"x": 1})))

    assert exc.value.version == version
    hint = exc.value.hint
    assert (hint.msg_id, hint.drone_id, hint.type, hint.correlation_id) == (
        "7a1c3e5f-2b4d-4c6e-8f10-000000000001",
        DRONE,
        "TAKEOFF",
        CORR,
    )


def test_unsupported_version_hint_leaves_unreadable_fields_empty() -> None:
    doc = {"schema_version": 7, "msg_id": "nope", "drone_id": "GW0.*", "type": 5}

    with pytest.raises(UnsupportedVersion) as exc:
        Codec().decode(_bytes(doc))

    hint = exc.value.hint
    assert (hint.msg_id, hint.drone_id, hint.type, hint.correlation_id) == (None,) * 4


def test_unknown_type_raises_with_the_envelope() -> None:
    with pytest.raises(UnknownType) as exc:
        Codec().decode(_bytes(_doc("BARREL_ROLL", {"spin": 3})))

    envelope = exc.value.envelope
    assert envelope.type == "BARREL_ROLL"
    assert envelope.msg_id == "7a1c3e5f-2b4d-4c6e-8f10-000000000001"
    assert envelope.drone_id == DRONE
    assert envelope.correlation_id == CORR


def test_invalid_payload_lists_at_most_5_problems_and_no_values() -> None:
    sentinel = "SENTINEL-do-not-echo"
    bad_waypoints = [{"lat": sentinel, "lon": 999, "altitude_m": 1}] * 4  # 12 problems
    doc = _doc("START_MISSION", {"mission_id": sentinel + " !", "waypoints": bad_waypoints})

    with pytest.raises(InvalidPayload) as exc:
        Codec().decode(_bytes(doc))

    errors = exc.value.errors
    assert len(errors) == 5
    assert errors[0] == "mission_id: string_pattern_mismatch"
    assert all(": " in e for e in errors)
    rendered = " ".join([str(exc.value), *errors])
    assert sentinel not in rendered
    assert "999" not in rendered
    assert exc.value.envelope.msg_id == "7a1c3e5f-2b4d-4c6e-8f10-000000000001"


def test_type_that_does_not_match_payload_shape_is_invalid_payload() -> None:
    with pytest.raises(InvalidPayload) as exc:
        Codec().decode(_bytes(_doc("ACK", {"altitude_m": 30})))

    assert set(exc.value.errors) == {"command_msg_id: missing", "command_type: missing"}


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"", "not_json"),
        (b"{not json", "not_json"),
        (b"\xff\xfe\x00garbage", "not_utf8"),
        (b"[1, 2, 3]", "not_object"),
        (b'"ARM"', "not_object"),
        (b'{"schema_version": 1, "schema_version": 1}', "not_json"),
    ],
    ids=["empty", "not-json", "not-utf8", "array", "string", "duplicate-key"],
)
def test_bytes_that_are_not_a_json_object_are_malformed(body: bytes, reason: str) -> None:
    with pytest.raises(MalformedMessage) as exc:
        Codec().decode(body)
    assert exc.value.reason == reason


def test_top_level_array_nested_9998_deep_is_malformed_not_a_crash() -> None:
    codec = Codec()
    body = b"[" * 9998 + b"]" * 9998  # 20 KB, under the size limit

    with pytest.raises(MalformedMessage) as exc:
        codec.decode(body)

    assert exc.value.reason == "not_json"
    assert codec.stats().rejected_by_reason == {"not_json": 1}


def test_payload_nested_10000_deep_is_malformed_not_a_crash() -> None:
    codec = Codec()
    body = _bytes(_doc())[:-2] + b'"x": ' + b"[" * 10_000 + b"]" * 10_000 + b"}}"

    with pytest.raises(MalformedMessage) as exc:
        codec.decode(body)

    assert exc.value.reason == "not_json"
    assert codec.stats().rejected_by_reason == {"not_json": 1}


@pytest.mark.parametrize("number", ["1e400", "-1e400", "1.5e999"])
@pytest.mark.parametrize("where", ["unknown-field", "known-field"])
def test_number_that_overflows_to_infinity_is_rejected_anywhere(number: str, where: str) -> None:
    if where == "unknown-field":
        text = json.dumps(_doc("POSITION", _position(extra="N")))
    else:
        text = json.dumps(_doc("POSITION", _position(lat="N")))
    body = text.replace('"N"', number).encode()

    with pytest.raises(MalformedMessage) as exc:
        Codec().decode(body)
    assert exc.value.reason == "not_json"


@pytest.mark.parametrize("where", ["unknown-field", "known-field", "key"])
def test_lone_surrogate_is_rejected_so_every_decoded_string_can_be_re_encoded(where: str) -> None:
    payload = {
        "unknown-field": {"command_msg_id": CMD_ID, "command_type": "ARM", "reason": "INJECTED",
                          "detail": "ok", "note": "S"},
        "known-field": {"command_msg_id": CMD_ID, "command_type": "ARM", "reason": "INJECTED",
                        "detail": "S"},
        "key": {"command_msg_id": CMD_ID, "command_type": "ARM", "reason": "INJECTED",
                "detail": "ok", "S": 1},
    }[where]  # fmt: skip
    body = json.dumps(_doc("NACK", payload)).replace('"S"', '"\\ud800"').encode()
    assert b"\\ud800" in body

    with pytest.raises(MalformedMessage) as exc:
        Codec().decode(body)
    assert exc.value.reason == "not_json"


def test_surrogate_pair_escape_is_a_normal_character_and_round_trips() -> None:
    codec = Codec()
    alert = {"code": "LOW_BATTERY", "severity": "WARNING", "message": "S", "value": None}
    body = json.dumps(_doc("ALERT", alert)).replace('"S"', '"\\ud83d\\ude81 low"').encode()

    msg = codec.decode(body)

    assert msg.payload.message == "\U0001f681 low"  # type: ignore[attr-defined]
    assert codec.decode(codec.encode(msg)) == msg


@pytest.mark.parametrize(
    "body", ["{}", None, 42, ["not", "bytes"]], ids=["str", "none", "int", "list"]
)
def test_input_that_is_not_bytes_is_malformed(body: Any) -> None:
    codec = Codec()
    with pytest.raises(MalformedMessage) as exc:
        codec.decode(body)
    assert exc.value.reason == "not_bytes"
    assert codec.stats().rejected_by_reason == {"not_bytes": 1}


@pytest.mark.parametrize("wrap", [bytes, bytearray, memoryview])
def test_bytes_like_input_is_accepted(wrap: Any) -> None:
    assert Codec().decode(wrap(_bytes(_doc()))).type == MessageType.ARM


def test_message_over_size_limit_is_rejected_before_parsing() -> None:
    # Invalid UTF-8 and invalid JSON: if it were parsed, the reason would differ.
    with pytest.raises(MalformedMessage) as exc:
        Codec().decode(b"\xff" * (MAX_MESSAGE_BYTES + 1))
    assert exc.value.reason == "too_large"


def test_message_of_exactly_the_size_limit_is_accepted() -> None:
    body = _bytes(_doc())
    padded = body[:-1] + b" " * (MAX_MESSAGE_BYTES - len(body)) + b"}"
    assert len(padded) == MAX_MESSAGE_BYTES

    assert Codec().decode(padded).type == MessageType.ARM


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_nan_or_infinite_number_is_rejected_on_decode(token: str) -> None:
    body = _bytes(_doc("POSITION", _position())).replace(
        b'"lat": 52.2297', f'"lat": {token}'.encode()
    )
    assert token.encode() in body

    with pytest.raises(MalformedMessage) as exc:
        Codec().decode(body)
    assert exc.value.reason == "not_json"


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nan_or_infinite_number_is_rejected_on_encode(value: float) -> None:
    with pytest.raises(ValidationError):
        PositionPayload(**_position(lat=value))  # typed construction refuses it

    sneaky = PositionPayload.model_construct(**_position(lat=value))
    msg = Message[BaseModel].model_construct(**(_doc("POSITION") | {"payload": sneaky}))
    msg = msg.model_copy(update={"type": MessageType.POSITION})
    with pytest.raises(EncodeError):
        Codec().encode(msg)


@pytest.mark.parametrize(
    "ts",
    [
        "2026-11-23T10:00:00.123",
        "2026-11-23T12:00:00.123+02:00",
        "2026-11-23 10:00:00.123Z",
        "2026-11-23T10:00:00Z",
        "2026-02-30T10:00:00.123Z",
    ],
    ids=["no-timezone", "not-utc", "no-T", "no-millis", "impossible-date"],
)
def test_ts_without_timezone_not_utc_or_not_a_real_date_is_malformed(ts: str) -> None:
    with pytest.raises(MalformedMessage) as exc:
        Codec().decode(_bytes(_doc(ts=ts)))
    assert exc.value.errors[0].startswith("ts: ")
    assert ts not in " ".join(exc.value.errors)


@pytest.mark.parametrize("msg_id", ["12345", "7A1C3E5F-2B4D-4C6E-8F10-000000000001", ""])
def test_msg_id_that_is_not_a_uuid_is_malformed(msg_id: str) -> None:
    with pytest.raises(MalformedMessage):
        Codec().decode(_bytes(_doc(msg_id=msg_id)))


@pytest.mark.parametrize("drone_id", ["gw0.d001", "gw0-*", "gw0-#", "GW0-D001", "-gw0", ""])
def test_drone_id_with_dot_star_hash_or_uppercase_is_malformed(drone_id: str) -> None:
    with pytest.raises(MalformedMessage) as exc:
        Codec().decode(_bytes(_doc(drone_id=drone_id)))
    assert exc.value.errors == ("drone_id: string_pattern_mismatch",)


@pytest.mark.parametrize("version", ["1", 1.5, True, None])
def test_schema_version_that_is_not_an_integer_is_malformed(version: Any) -> None:
    with pytest.raises(MalformedMessage) as exc:
        Codec().decode(_bytes(_doc(schema_version=version)))
    assert exc.value.reason == "invalid_envelope"


def test_explicit_null_for_optional_field_is_invalid_payload() -> None:
    doc = _doc("GOTO", {"lat": 1, "lon": 2, "altitude_m": 30, "speed_mps": None})
    with pytest.raises(InvalidPayload) as exc:
        Codec().decode(_bytes(doc))
    assert exc.value.errors == ("<root>: speed_mps may be absent but not null",)


def test_duplicate_msg_id_is_valid_for_the_codec() -> None:
    codec = Codec()
    body = _bytes(_doc())
    assert codec.decode(body) == codec.decode(body)


# -- encode refuses invalid messages ------------------------------------------------------


def _ack() -> AckPayload:
    return AckPayload(command_msg_id=CMD_ID, command_type=CommandType.ARM)


@pytest.mark.parametrize(
    "update",
    [
        {"drone_id": "GW0.D001"},
        {"ts": "yesterday"},
        {"correlation_id": "not-a-uuid"},
        {"type": MessageType.ARM},  # the ACK payload no longer matches the type
        {"schema_version": 2},
        {"payload": AckPayload.model_construct(command_type=CommandType.ARM)},  # field missing
        {"payload": GeoPoint(lat=1, lon=2)},  # not a payload class
    ],
    ids=["drone-id", "ts", "correlation-id", "type", "version", "payload-missing-field",
         "payload-wrong-class"],
)  # fmt: skip
def test_encode_refuses_an_invalid_message(update: dict[str, Any]) -> None:
    codec = Codec()
    # model_copy(update=...) skips validation, like a buggy producer would.
    msg = codec.new(MessageType.ACK, DRONE, _ack(), CORR).model_copy(update=update)

    with pytest.raises(EncodeError):
        codec.encode(msg)
    assert (codec.stats().encoded, codec.stats().encode_errors) == (0, 1)


def test_encode_refuses_something_that_is_not_a_message() -> None:
    with pytest.raises(EncodeError):
        Codec().encode({"type": "ARM"})  # type: ignore[arg-type]


def test_new_refuses_invalid_fields_and_wrong_payload_class() -> None:
    codec = Codec()
    with pytest.raises(EncodeError) as exc:
        codec.new(MessageType.ARM, DRONE, TakeoffPayload(altitude_m=30), None)
    assert exc.value.errors == ("<root>: ARM needs payload ArmPayload",)

    with pytest.raises(EncodeError):
        codec.new(MessageType.ARM, "GW0.D001", ArmPayload(), None)
    with pytest.raises(EncodeError):
        codec.new(MessageType.ARM, DRONE, ArmPayload(), "not-a-uuid")
    with pytest.raises(EncodeError):
        codec.new(MessageType.ARM, DRONE, ArmPayload(), None, ts=datetime(2026, 1, 1))


def test_payload_and_message_are_immutable() -> None:
    msg = Codec().new(MessageType.ARM, DRONE, ArmPayload(), None)
    with pytest.raises(ValidationError):
        msg.drone_id = "gw0-d002"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        msg.payload.__setattr__("anything", 1)


# -- routing key and AMQP properties ------------------------------------------------------


def test_routing_key_is_drone_id_and_uppercase_type() -> None:
    assert Codec.routing_key("gw0-d001", MessageType.ARM) == "drone.gw0-d001.ARM"
    assert Codec.routing_key("gw12-d100", "MISSION_STATUS") == "drone.gw12-d100.MISSION_STATUS"


@pytest.mark.parametrize(
    ("drone_id", "type_"),
    [("gw0.d001", "ARM"), ("gw0-*", "ARM"), ("gw0-#", "ARM"), ("GW0", "ARM"), ("gw0", "arm")],
)
def test_routing_key_refuses_words_that_are_not_safe(drone_id: str, type_: str) -> None:
    with pytest.raises(ValueError, match="routing-key word"):
        Codec.routing_key(drone_id, type_)


@pytest.mark.parametrize("persistent", [True, False])
def test_amqp_properties_mirror_the_envelope(persistent: bool) -> None:
    when = datetime(2026, 11, 23, 10, 0, 0, tzinfo=UTC)
    msg = Codec().new(MessageType.ACK, DRONE, _ack(), CORR, ts=when)

    props = Codec.amqp_properties(msg, persistent=persistent)

    assert props.content_type == "application/json"
    assert props.message_id == msg.msg_id
    assert props.correlation_id == CORR
    assert props.type == "ACK"
    assert props.timestamp == int(when.timestamp())
    assert props.headers == {"schema_version": 1}
    expected = pika.DeliveryMode.Persistent if persistent else pika.DeliveryMode.Transient
    assert props.delivery_mode == expected.value


# -- counters and configuration -----------------------------------------------------------


def test_stats_count_decodes_encodes_and_rejects_by_reason() -> None:
    codec = Codec()
    codec.encode(codec.decode(_bytes(_doc())))
    for body in (b"", b"[]", _bytes(_doc("NOPE")), _bytes(_doc(schema_version=9))):
        with pytest.raises(DecodeError):
            codec.decode(body)

    stats = codec.stats()
    assert (stats.decoded, stats.encoded, stats.encode_errors) == (1, 1, 0)
    assert stats.rejected_by_reason == {
        "not_json": 1,
        "not_object": 1,
        "unknown_type": 1,
        "unsupported_version": 1,
    }


def test_codec_refuses_versions_it_has_no_schemas_for() -> None:
    with pytest.raises(ValueError, match="no schemas"):
        Codec(read_versions=frozenset({1, 2}))
    with pytest.raises(ValueError, match="no schemas"):
        Codec(write_version=0)
