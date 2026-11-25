"""The command flow (m2-design section 6): validate through the Codec, store the command and
its audit entry in one transaction, publish through the ``CommandPublisher``, then move the
command to SENT (broker-confirmed) or FAILED.
"""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import UTC, datetime
from typing import Any, Final

from pydantic import BaseModel, ValidationError
from skyhaul_contracts.codec import Codec
from skyhaul_contracts.errors import EncodeError
from skyhaul_contracts.message import Message
from skyhaul_contracts.types import PAYLOADS, MessageType
from skyhaul_fleet_core.models import Command, CommandStatus
from skyhaul_fleet_core.transitions import cas_update
from sqlalchemy import select
from sqlalchemy.orm import Session

from skyhaul_api.audit.service import record
from skyhaul_api.commands.publisher import CommandPublisher, Outcome, PublisherFull
from skyhaul_api.errors import ApiError

log = logging.getLogger(__name__)

# Extra time for the request thread beyond the publisher's deadline: the publisher resolves
# every future itself, this only guards against a publisher thread that died.
RESULT_GRACE_S: Final = 3.0


def build_message(
    codec: Codec, type_name: str, drone_id: str, payload: dict[str, Any], correlation_id: str
) -> tuple[Message[BaseModel], bytes]:
    """The command message and its wire bytes. 422 with field paths, never input values."""
    msg_type = MessageType(type_name)
    model = PAYLOADS[(codec.write_version, msg_type)]
    try:
        # The Codec's own payload check on decode: the same model, the same lax mode.
        validated = model.model_validate(payload)
    except ValidationError as exc:
        raise invalid(_pydantic_fields(exc)) from None
    try:
        message = codec.new(msg_type, drone_id, validated, correlation_id)
        return message, codec.encode(message)
    except EncodeError as exc:
        raise invalid(_encode_fields(exc.errors)) from None


def invalid(fields: dict[str, list[str]]) -> ApiError:
    return ApiError(422, "validation_failed", "The request failed validation.", fields)


def _pydantic_fields(exc: ValidationError) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    for err in exc.errors(include_url=False, include_input=False, include_context=False):
        kind = err["type"]
        loc = "<extra>" if kind == "extra_forbidden" else ".".join(map(str, err["loc"]))
        path = f"payload.{loc}" if loc else "payload"
        fields.setdefault(path, []).append(kind)
    return fields


def _encode_fields(errors: tuple[str, ...]) -> dict[str, list[str]]:
    """``"<path>: <kind>"`` items of an ``EncodeError`` (they never hold input values)."""
    fields: dict[str, list[str]] = {}
    for item in errors:
        path, _, kind = item.partition(": ")
        fields.setdefault(path, []).append(kind or "invalid")
    return fields


def find_by_key(session: Session, user_id: uuid.UUID, key: str) -> Command | None:
    return session.scalars(
        select(Command).where(Command.requested_by == user_id, Command.idempotency_key == key)
    ).first()


def replay(existing: Command, message: Message[BaseModel]) -> Command:
    """The original command of an Idempotency-Key replay. The same key for another drone,
    type or payload is a client bug: 409. Payloads are compared after Codec validation, so
    ``40`` and ``40.0`` are the same altitude."""
    same = (
        existing.drone_id == message.drone_id
        and existing.type == message.type.value
        and existing.payload == stored_payload(message)
    )
    if not same:
        raise ApiError(409, "idempotency_key_reused", "The key was used for another command.")
    return existing


def stored_payload(message: Message[BaseModel]) -> dict[str, Any]:
    payload: dict[str, Any] = message.payload.model_dump(mode="json", exclude_unset=True)
    return payload


def store(
    session: Session,
    message: Message[BaseModel],
    *,
    user_id: uuid.UUID,
    idempotency_key: str | None,
) -> Command:
    """Insert the PENDING command and its audit entry, and commit (one transaction)."""
    command = Command(
        id=uuid.UUID(message.msg_id),
        drone_id=message.drone_id,
        type=message.type.value,
        payload=stored_payload(message),
        correlation_id=uuid.UUID(str(message.correlation_id)),
        status=CommandStatus.PENDING,
        requested_by=user_id,
        idempotency_key=idempotency_key,
    )
    session.add(command)
    session.flush()
    record(
        session,
        action="command.send",
        outcome="success",
        actor=user_id,
        target_type="command",
        target_id=str(command.id),
        data={"drone_id": command.drone_id, "type": command.type},
    )
    session.commit()
    return command


def publish(publisher: CommandPublisher, message: Message[BaseModel], body: bytes) -> Outcome:
    try:
        future = publisher.submit(message, body)
    except PublisherFull:
        log.warning("command publisher queue full", extra={"correlation_id": message.msg_id})
        return Outcome.UNREACHABLE
    try:
        return future.result(timeout=publisher.deadline_s + RESULT_GRACE_S)
    except FutureTimeout:
        log.error("command publisher did not resolve a publish")
        return Outcome.UNREACHABLE


def finish(session: Session, command: Command, outcome: Outcome) -> Command:
    """SENT if confirmed, FAILED otherwise. A reply may have overtaken the confirm; the CAS
    then changes nothing and the reply's status stands."""
    if outcome is Outcome.CONFIRMED:
        cas_update(session, Command, command.id, CommandStatus.SENT, sent_at=datetime.now(UTC))
    else:
        cas_update(session, Command, command.id, CommandStatus.FAILED)
    session.commit()
    session.refresh(command)
    if outcome is not Outcome.CONFIRMED:
        raise ApiError(503, "command_not_sent", "The broker did not confirm the command.")
    return command
