"""``Message[P]``: a validated, immutable message with a typed payload."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, SerializeAsAny, field_validator, model_validator

from skyhaul_contracts.types import PAYLOADS, MessageType
from skyhaul_contracts.v1._generated.common_schema import DroneId, Timestamp, Uuid

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"


def format_ts(when: datetime) -> str:
    """Wire form of a timestamp: UTC, millisecond precision, ``Z`` suffix."""
    if when.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return when.astimezone(UTC).strftime(TS_FORMAT)[:-3] + "Z"


def parse_ts(ts: str) -> datetime:
    """Parse a wire timestamp. Raises ValueError for impossible dates such as 02-30."""
    try:
        return datetime.strptime(ts.removesuffix("Z"), TS_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        # strptime's own message repeats the input; ours must not (errors.py).
        raise ValueError("not a real calendar date") from None


class Message[P: BaseModel](BaseModel):
    """Envelope fields plus a payload model. Built by ``Codec.new`` or ``Codec.decode``.

    Construction validates the envelope fields, checks the timestamp is a real date and
    checks the payload class is the one the catalogue lists for ``(schema_version, type)``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    msg_id: Uuid
    correlation_id: Uuid | None
    drone_id: DroneId
    type: MessageType
    schema_version: int
    ts: Timestamp
    payload: SerializeAsAny[P]  # serialize the real payload class, not BaseModel

    @field_validator("ts")
    @classmethod
    def _real_date(cls, ts: str) -> str:
        parse_ts(ts)  # the schema pattern cannot rule out dates like 2026-02-30
        return ts

    @model_validator(mode="after")
    def _payload_matches_type(self) -> Self:
        expected = PAYLOADS.get((self.schema_version, self.type))
        if expected is None:
            raise ValueError(f"no payload schema for {self.type} in v{self.schema_version}")
        if type(self.payload) is not expected:
            raise ValueError(f"{self.type} needs payload {expected.__name__}")
        return self
