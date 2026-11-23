"""The command handler registry.

Handlers are registered with `@registry.handles("ARM")` at import time. A handler
gets the board's `World` (the lock is already held), the command and the current
monotonic time, and returns its replies. It has no outbox or channel, so it cannot
publish, and it must not sleep or do I/O.

Until the contracts package (#6) lands, commands are the local `Command` type and
command types are plain strings. The wiring PR switches them to
`skyhaul_contracts.Message` and `CommandType`.
"""

import copy
from collections.abc import Callable
from dataclasses import dataclass, replace

from skyhaul_drone_sim.board import World
from skyhaul_drone_sim.outbox import Outgoing, is_outgoing_list

type CommandType = str

_MAX_COMMAND_TYPE_LEN = 32
_INTERNAL_ERROR_DETAIL = "the command could not be handled"


@dataclass(frozen=True, slots=True)
class Command:
    """A decoded command. Stand-in for `skyhaul_contracts.Message` until #6 lands."""

    msg_id: str
    correlation_id: str | None
    drone_id: str
    type: CommandType
    payload: object


type Handler = Callable[[World, Command, float], list[Outgoing]]


class DuplicateHandlerError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class NackPlaceholder:
    """PLACEHOLDER for the contract NACK payload.

    The wiring PR after #6 replaces it with the generated NACK model, built through
    the Codec. `error` is never sent: it carries the handler's exception so the
    consumer can log the traceback after the board lock is released (no logging
    under the lock).
    """

    command_msg_id: str
    command_type: str
    reason: str
    detail: str
    error: Exception | None = None


def _nack(command: Command, reason: str, detail: str, error: Exception | None = None) -> Outgoing:
    payload = NackPlaceholder(
        command_msg_id=command.msg_id,
        command_type=command.type[:_MAX_COMMAND_TYPE_LEN],
        reason=reason,
        detail=detail,
        error=error,
    )
    return Outgoing(
        drone_id=command.drone_id,
        type="NACK",
        correlation_id=command.correlation_id,
        payload=payload,
        persistent=True,
    )


class Registry:
    """Maps each command type to its one handler.

    A handler that fails (raises, or returns anything but a list of Outgoing) has
    its World changes discarded, so the NACK it gets matches the drone's state.
    """

    def __init__(self) -> None:
        self._handlers: dict[CommandType, Handler] = {}

    def handles(self, command_type: CommandType) -> Callable[[Handler], Handler]:
        """Register the decorated function as the handler for `command_type`.

        Raises DuplicateHandlerError if the type already has a handler, so a second
        registration fails when its module is imported.
        """

        def register(handler: Handler) -> Handler:
            existing = self._handlers.get(command_type)
            if existing is not None:
                raise DuplicateHandlerError(
                    f"command type {command_type!r} already has a handler "
                    f"({_name(existing)}); cannot also register {_name(handler)}"
                )
            self._handlers[command_type] = handler
            return handler

        return register

    def command_types(self) -> frozenset[CommandType]:
        return frozenset(self._handlers)

    def dispatch(self, world: World, command: Command, now: float) -> list[Outgoing]:
        """Run the handler for `command.type` and return its replies.

        Called inside a board transaction, so it never logs, sleeps or publishes. A
        handler that raises, or returns anything but a list of Outgoing, gets an
        INTERNAL_ERROR NACK instead of propagating, and every change staged in the
        World is discarded (call dispatch once per transaction). The detail is a
        fixed string; the exception rides on the placeholder for the consumer to
        log with its traceback.

        Steps added by later PRs, in this order (approved design):
        1. Unknown drone: return [] (#9).
        2. Fault gate: link loss returns [], NACK_NEXT returns a NACK INJECTED (#10).
           It runs before the dedup cache, so link loss drops duplicates too.
        3. Dedup by msg_id: return the cached replies.
        4. Handler (this PR).
        5. Cache the replies.
        6. REPLY_DELAY fault: schedule the replies and return [] (#10).
        """
        handler = self._handlers.get(command.type)
        if handler is None:
            return [_nack(command, "UNKNOWN_TYPE", "no handler for this command type")]
        try:
            replies = handler(world, command, now)
            if not is_outgoing_list(replies):
                raise TypeError(
                    f"handler returned {type(replies).__name__}, expected list[Outgoing]"
                )
            # Copies, so the publisher never reads live job state.
            return [replace(reply, payload=copy.deepcopy(reply.payload)) for reply in replies]
        except Exception as exc:
            world.discard()
            return [_nack(command, "INTERNAL_ERROR", _INTERNAL_ERROR_DETAIL, exc)]


def _name(handler: Handler) -> str:
    return getattr(handler, "__qualname__", repr(handler))
