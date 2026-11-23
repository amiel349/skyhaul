"""The Codec: the only place that turns messages into bytes and back (ADR 0002, ADR 0005).

``decode`` checks, in order: size, UTF-8, strict JSON, ``schema_version``, the closed
envelope, the type catalogue, then the payload. ``encode`` serializes and then runs the same
decode checks on its own output, so nothing invalid can leave either side.

Thread safety: one ``threading.Lock`` per instance, held for the whole ``encode`` or
``decode`` call. It guards the counters. The models themselves are immutable. Never call the
codec while holding another lock (simulator invariant 8 in docs/architecture/m1-design.md).
"""

from __future__ import annotations

import json
import math
import re
import threading
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, NoReturn

import pika
from pydantic import BaseModel, TypeAdapter, ValidationError

from skyhaul_contracts import topology
from skyhaul_contracts.errors import (
    DecodeError,
    EncodeError,
    EnvelopeHint,
    InvalidPayload,
    MalformedMessage,
    UnknownType,
    UnsupportedVersion,
)
from skyhaul_contracts.message import Message, format_ts, parse_ts
from skyhaul_contracts.types import (
    CURRENT_VERSION,
    ENVELOPES,
    PAYLOADS,
    SUPPORTED_VERSIONS,
    MessageType,
)
from skyhaul_contracts.v1._generated.common_schema import DroneId, TypeName, Uuid

MAX_MESSAGE_BYTES: Final = 64 * 1024
"""Largest accepted message body, in bytes. Checked before parsing on decode and after
serializing on encode. The largest legal v1 message (START_MISSION, 50 waypoints) is
about 4 KB."""

MAX_ERRORS: Final = 5
"""Most problems listed in an InvalidPayload or EncodeError."""

CONTENT_TYPE: Final = "application/json"

_UUID: Final = TypeAdapter(Uuid)
_DRONE_ID: Final = TypeAdapter(DroneId)
_TYPE_NAME: Final = TypeAdapter(TypeName)


@dataclass(frozen=True, slots=True)
class CodecStats:
    """A consistent snapshot of the codec counters."""

    decoded: int
    encoded: int
    encode_errors: int
    rejected_by_reason: dict[str, int] = field(default_factory=dict)


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(f"{name} is not valid JSON")


def _finite_float(text: str) -> float:
    """``parse_float`` hook: ``1e400`` would otherwise become infinity, anywhere in the body."""
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("number out of range")
    return value


# A lone surrogate can only come from a \uD800-\uDFFF escape: strict UTF-8 decoding already
# rejects encoded ones. Cheap pre-check on the text, then an exact check on the parsed strings.
_SURROGATE_ESCAPE = re.compile(r"\\u[dD][89a-fA-F]")
_SURROGATE = re.compile("[\ud800-\udfff]")


def _has_lone_surrogate(obj: Any) -> bool:
    """Iterative, so a deeply nested body cannot overflow the stack here."""
    stack = [obj]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if _SURROGATE.search(item):
                return True
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return False


def parse_json(body: bytes | bytearray | memoryview) -> Any:
    """Strict JSON for message bodies. Raises ``MalformedMessage``.

    Beyond RFC 8259: no NaN/Infinity tokens, no number that overflows to infinity, no
    duplicate keys, and no lone surrogate (I-JSON, RFC 7493), so every string we accept can
    be encoded as UTF-8 again. Nesting deeper than the parser's recursion limit is
    ``not_json``, not a ``RecursionError``.
    """
    if not isinstance(body, bytes | bytearray | memoryview):
        raise MalformedMessage("not_bytes")
    view = memoryview(body)
    if view.nbytes > MAX_MESSAGE_BYTES:
        raise MalformedMessage("too_large")
    try:
        text = view.tobytes().decode("utf-8")
    except UnicodeDecodeError:
        raise MalformedMessage("not_utf8") from None
    try:
        obj = json.loads(
            text,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
            object_pairs_hook=_no_duplicate_keys,
        )
    except (ValueError, RecursionError):  # bad syntax, NaN, overflow, duplicates, depth
        raise MalformedMessage("not_json") from None
    if _SURROGATE_ESCAPE.search(text) and _has_lone_surrogate(obj):
        raise MalformedMessage("not_json")
    return obj


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError("duplicate key")
    return obj


def _summarize(exc: ValidationError) -> tuple[str, ...]:
    """``"<path>: <kind>"`` for the first few problems. Never includes input values."""
    out: list[str] = []
    for err in exc.errors(include_url=False, include_input=False, include_context=False):
        kind = err["type"]
        # The location of an unexpected key is the key itself, which is sender input.
        path = "<extra>" if kind == "extra_forbidden" else ".".join(map(str, err["loc"]))
        if kind == "value_error":
            # Raised by our own validators (message.py), whose text never holds input.
            kind = err["msg"].removeprefix("Value error, ")
        out.append(f"{path or '<root>'}: {kind}")
        if len(out) == MAX_ERRORS:
            break
    return tuple(out)


def _read_field(adapter: TypeAdapter[str], value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return adapter.validate_python(value, strict=True)
    except ValidationError:
        return None


def _hint(obj: dict[str, Any]) -> EnvelopeHint:
    return EnvelopeHint(
        msg_id=_read_field(_UUID, obj.get("msg_id")),
        drone_id=_read_field(_DRONE_ID, obj.get("drone_id")),
        type=_read_field(_TYPE_NAME, obj.get("type")),
        correlation_id=_read_field(_UUID, obj.get("correlation_id")),
    )


class Codec:
    """Encode and decode drone messages. One instance can be shared by many threads."""

    def __init__(
        self,
        *,
        write_version: int = CURRENT_VERSION,
        read_versions: frozenset[int] = SUPPORTED_VERSIONS,
    ) -> None:
        unknown = (read_versions | {write_version}) - ENVELOPES.keys()
        if unknown:
            raise ValueError(f"no schemas for schema_version {sorted(unknown)}")
        if write_version not in read_versions:
            raise ValueError("write_version must be one of read_versions")
        self.write_version = write_version
        self.read_versions = read_versions
        self._lock = threading.Lock()
        self._decoded = 0
        self._encoded = 0
        self._encode_errors = 0
        self._rejected: Counter[str] = Counter()

    # -- public API ------------------------------------------------------------------------

    def decode(self, body: bytes) -> Message[BaseModel]:
        """Bytes from the wire to a validated message. Raises a ``DecodeError`` subclass for
        any input, including non-bytes (``not_bytes``)."""
        with self._lock:
            try:
                msg = self._decode(body)
            except DecodeError as exc:
                self._rejected[exc.reason] += 1
                raise
            self._decoded += 1
            return msg

    def encode(self, msg: Message[BaseModel]) -> bytes:
        """A message to compact UTF-8 JSON. Raises ``EncodeError`` if it would not decode."""
        with self._lock:
            try:
                body = self._encode(msg)
            except EncodeError:
                self._encode_errors += 1
                raise
            self._encoded += 1
            return body

    def new(
        self,
        type: MessageType,
        drone_id: str,
        payload: BaseModel,
        correlation_id: str | None,
        *,
        ts: datetime | None = None,
    ) -> Message[BaseModel]:
        """Build a message with a fresh ``msg_id``, ``ts`` (now, unless given) and the write
        version. Raises ``EncodeError`` when a field or the payload class is wrong."""
        try:
            return Message[BaseModel](
                msg_id=str(uuid.uuid4()),
                correlation_id=correlation_id,
                drone_id=drone_id,
                type=type,
                schema_version=self.write_version,
                ts=format_ts(ts or datetime.now(UTC)),
                payload=payload,
            )
        except ValidationError as exc:
            raise EncodeError(str(type), _summarize(exc)) from None
        except ValueError as exc:  # naive datetime
            raise EncodeError(str(type), (f"ts: {exc}",)) from None

    @staticmethod
    def routing_key(drone_id: str, type: str) -> str:
        """``drone.<id>.<TYPE>``. Same as ``topology.routing_key``."""
        return topology.routing_key(drone_id, type)

    @staticmethod
    def amqp_properties(msg: Message[BaseModel], *, persistent: bool) -> Any:
        """``pika.BasicProperties`` mirroring the envelope, so the management UI is readable.
        The body stays the source of truth."""
        return pika.BasicProperties(
            content_type=CONTENT_TYPE,
            message_id=msg.msg_id,
            correlation_id=msg.correlation_id,
            type=msg.type.value,
            timestamp=int(parse_ts(msg.ts).timestamp()),
            headers={"schema_version": msg.schema_version},
            delivery_mode=pika.DeliveryMode.Persistent
            if persistent
            else pika.DeliveryMode.Transient,
        )

    def stats(self) -> CodecStats:
        with self._lock:
            return CodecStats(
                decoded=self._decoded,
                encoded=self._encoded,
                encode_errors=self._encode_errors,
                rejected_by_reason=dict(self._rejected),
            )

    # -- internals (caller holds self._lock) ----------------------------------------------

    def _decode(self, body: bytes) -> Message[BaseModel]:
        obj = parse_json(body)
        if not isinstance(obj, dict):
            raise MalformedMessage("not_object")

        version = obj.get("schema_version")
        if not isinstance(version, int) or isinstance(version, bool):
            kind = "missing" if "schema_version" not in obj else "int_type"
            raise MalformedMessage("invalid_envelope", (f"schema_version: {kind}",))
        if version not in self.read_versions:
            raise UnsupportedVersion(version, _hint(obj))

        try:
            envelope = ENVELOPES[version].model_validate(obj, strict=True)
            parse_ts(envelope.ts)
        except ValidationError as exc:
            raise MalformedMessage("invalid_envelope", _summarize(exc)) from None
        except ValueError:
            raise MalformedMessage("invalid_envelope", ("ts: invalid_date",)) from None

        try:
            msg_type = MessageType(envelope.type)
            payload_model = PAYLOADS[(version, msg_type)]
        except (ValueError, KeyError):
            raise UnknownType(envelope) from None

        try:
            # Validate the object already parsed, never a re-serialized copy: unknown fields
            # are then ignored whatever they hold, exactly as the schema ignores them.
            # Lax mode only reaches enums (matched by exact value) and nested models; every
            # scalar is a generated Strict* type (--strict-types), so "1" is never 1.
            payload = payload_model.model_validate(envelope.payload)
        except ValidationError as exc:
            raise InvalidPayload(envelope, _summarize(exc)) from None

        return Message[BaseModel](
            msg_id=envelope.msg_id,
            correlation_id=envelope.correlation_id,
            drone_id=envelope.drone_id,
            type=msg_type,
            schema_version=version,
            ts=envelope.ts,
            payload=payload,
        )

    def _encode(self, msg: Message[BaseModel]) -> bytes:
        if not isinstance(msg, Message):
            raise EncodeError("", ("<root>: not a Message",))
        type_name = str(getattr(msg, "type", ""))
        if getattr(msg, "schema_version", None) != self.write_version:
            raise EncodeError(type_name, ("schema_version: not the write version",))
        try:
            # Optional payload fields default to None and are left out (never null on the wire).
            doc = msg.model_dump(mode="json", exclude_defaults=True, warnings="error")
            body = json.dumps(
                doc, allow_nan=False, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        except (ValueError, TypeError):  # NaN/Infinity, or a model_construct'ed value
            raise EncodeError(type_name, ("<root>: not serializable",)) from None
        if len(body) > MAX_MESSAGE_BYTES:
            raise EncodeError(type_name, ("<root>: too_large",))
        try:
            again = self._decode(body)
        except DecodeError as exc:
            raise EncodeError(type_name, exc.errors or (f"<root>: {exc.reason}",)) from None
        if again != msg:
            raise EncodeError(type_name, ("<root>: does not round-trip",))
        return body
