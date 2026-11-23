"""Typed codec errors. Each one carries what the caller needs to react (ADR 0006).

Error details name fields and error kinds only. They never echo the offending values, so a
NACK ``detail`` or a log line built from them cannot leak or reflect message content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from skyhaul_contracts.v1 import Envelope

NACK_DETAIL_MAX_LENGTH: Final = 256
"""``maxLength`` of NACK ``detail`` in v1 (a contract test keeps it equal to the schema)."""

MalformedReason = Literal[
    "not_bytes", "too_large", "not_utf8", "not_json", "not_object", "invalid_envelope"
]


@dataclass(frozen=True, slots=True)
class EnvelopeHint:
    """Envelope fields read without the version-specific schema. A field is None when it
    is missing or does not match its v1 pattern."""

    msg_id: str | None
    drone_id: str | None
    type: str | None
    correlation_id: str | None


class CodecError(Exception):
    """Base class for every codec error."""


class DecodeError(CodecError):
    """The bytes are not a message this codec accepts. ``reason`` keys the codec counters."""

    reason: str

    def __init__(self, reason: str, message: str, errors: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.reason = reason
        self.errors = errors

    @property
    def detail(self) -> str:
        """Text for a NACK ``detail``: the message and the problems, never input values,
        and never longer than ``NACK_DETAIL_MAX_LENGTH``."""
        text = f"{self}: {'; '.join(self.errors)}" if self.errors else str(self)
        if len(text) <= NACK_DETAIL_MAX_LENGTH:
            return text
        return text[: NACK_DETAIL_MAX_LENGTH - 1] + "\u2026"


class MalformedMessage(DecodeError):
    """No trustworthy envelope: not bytes, too large, not UTF-8, not JSON, or an invalid
    envelope.

    Consumers reject it without requeue (dead letter). There is nobody to NACK."""

    def __init__(self, reason: MalformedReason, errors: tuple[str, ...] = ()) -> None:
        super().__init__(reason, f"malformed message: {reason}", errors)


class UnsupportedVersion(DecodeError):
    """``schema_version`` is an integer outside the read set (N-1 and N).

    The message text does not contain the version: it is sender input and can be an
    integer thousands of digits long. ``version`` holds it for the caller; do not echo it."""

    def __init__(self, version: int, hint: EnvelopeHint) -> None:
        super().__init__("unsupported_version", "unsupported schema_version")
        self.version = version
        self.hint = hint


class UnknownType(DecodeError):
    """Valid envelope, but the catalogue has no such type for this version."""

    def __init__(self, envelope: Envelope) -> None:
        super().__init__("unknown_type", f"unknown message type {envelope.type}")
        self.envelope = envelope


class InvalidPayload(DecodeError):
    """Valid envelope and known type, but the payload breaks its schema.

    ``errors`` holds at most 5 entries of the form ``"<field path>: <error kind>"``."""

    def __init__(self, envelope: Envelope, errors: tuple[str, ...]) -> None:
        super().__init__("invalid_payload", f"invalid {envelope.type} payload", errors)
        self.envelope = envelope


class EncodeError(CodecError):
    """The message cannot be sent: it would not pass our own decoder. A producer bug."""

    def __init__(self, type: str, errors: tuple[str, ...]) -> None:
        super().__init__(f"refusing to encode invalid {type or 'message'}")
        self.type = type
        self.errors = errors
