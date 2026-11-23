"""Skyhaul drone message contracts: the Codec, message types and RabbitMQ topology.

Schemas live in ``contracts/schemas/``; payload models are generated into
``skyhaul_contracts.v1``. See ``contracts/README.md`` for the versioning rule.
"""

from skyhaul_contracts.codec import MAX_MESSAGE_BYTES, Codec, CodecStats
from skyhaul_contracts.errors import (
    CodecError,
    DecodeError,
    EncodeError,
    EnvelopeHint,
    InvalidPayload,
    MalformedMessage,
    UnknownType,
    UnsupportedVersion,
)
from skyhaul_contracts.message import Message
from skyhaul_contracts.types import (
    CURRENT_VERSION,
    SUPPORTED_VERSIONS,
    AlertCode,
    AlertSeverity,
    CommandType,
    FlightState,
    MessageType,
    MissionState,
    NackReason,
    TelemetryType,
)

__all__ = [
    "CURRENT_VERSION",
    "MAX_MESSAGE_BYTES",
    "SUPPORTED_VERSIONS",
    "AlertCode",
    "AlertSeverity",
    "Codec",
    "CodecError",
    "CodecStats",
    "CommandType",
    "DecodeError",
    "EncodeError",
    "EnvelopeHint",
    "FlightState",
    "InvalidPayload",
    "MalformedMessage",
    "Message",
    "MessageType",
    "MissionState",
    "NackReason",
    "TelemetryType",
    "UnknownType",
    "UnsupportedVersion",
]
