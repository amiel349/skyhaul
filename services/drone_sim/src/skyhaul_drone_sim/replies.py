"""ACK and NACK replies. Both are persistent and carry the command's correlation_id and
msg_id (design note invariant 10)."""

from typing import Any

from skyhaul_contracts import CommandType, Message, MessageType, NackReason
from skyhaul_contracts.v1 import AckPayload, NackPayload

from skyhaul_drone_sim.outbox import Outgoing, new_message


def ack(command: Message[Any]) -> Outgoing:
    payload = AckPayload(command_msg_id=command.msg_id, command_type=CommandType(command.type))
    return Outgoing(
        new_message(MessageType.ACK, command.drone_id, payload, command.correlation_id),
        persistent=True,
    )


def nack(
    *,
    drone_id: str,
    correlation_id: str | None,
    command_msg_id: str,
    command_type: str,
    reason: NackReason,
    detail: str,
) -> Outgoing:
    """A NACK. `command_type` is the type as received ("" when unreadable); `detail` must be
    fixed text or a `DecodeError.detail`, never input values."""
    payload = NackPayload(
        command_msg_id=command_msg_id, command_type=command_type, reason=reason, detail=detail
    )
    return Outgoing(
        new_message(MessageType.NACK, drone_id, payload, correlation_id), persistent=True
    )


def nack_command(command: Message[Any], reason: NackReason, detail: str) -> Outgoing:
    """A NACK for a decoded command."""
    return nack(
        drone_id=command.drone_id,
        correlation_id=command.correlation_id,
        command_msg_id=command.msg_id,
        command_type=command.type.value,
        reason=reason,
        detail=detail,
    )
