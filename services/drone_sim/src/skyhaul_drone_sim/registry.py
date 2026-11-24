"""The command handler registry.

Handlers are registered with `@registry.handles(CommandType.ARM)` at import time. A handler
gets the board's `World` (the lock is already held), the decoded command and the current
monotonic time, and returns its replies. It has no outbox or channel, so it cannot publish,
and it must not sleep or do I/O.
"""

import copy
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Final

from skyhaul_contracts import CommandType, Message, MessageType, NackReason

from skyhaul_drone_sim.board import World
from skyhaul_drone_sim.faults import link_lost, reply_delay_s, take_injected_nack
from skyhaul_drone_sim.model import Drone
from skyhaul_drone_sim.namespaces import namespace_of
from skyhaul_drone_sim.outbox import Outgoing, is_outgoing_list
from skyhaul_drone_sim.replies import nack_command

type Handler = Callable[[World, Message[Any], float], list[Outgoing]]

INTERNAL_ERROR_DETAIL: Final = "the command could not be handled"
UNKNOWN_TYPE_DETAIL: Final = "no handler for this command type"
INJECTED_DETAIL: Final = "rejected by an injected NACK_NEXT fault"
DELAY_FULL_DETAIL: Final = "too many delayed replies waiting in this namespace"
_REPLY_TYPES: Final = frozenset({MessageType.ACK, MessageType.NACK})


class DuplicateHandlerError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """What one `dispatch` produced.

    `error` is the handler's exception, never sent: the NACK carries a fixed detail, and the
    consumer logs the traceback after the board lock is released (no logging under the lock).
    """

    replies: list[Outgoing] = field(default_factory=list)
    error: Exception | None = None
    unknown_drone: bool = False
    duplicate: bool = False
    """The msg_id was handled before: `replies` are the cached ones, nothing was applied."""
    link_lost: bool = False
    """The drone is in LINK_LOSS: nothing was applied, cached or answered."""
    delayed: bool = False
    """REPLY_DELAY: the replies were scheduled on the board, so `replies` is empty."""


class Registry:
    """Maps each command type to its one handler.

    A handler that fails (raises, or returns anything but a list of Outgoing) has its World
    changes discarded, so the NACK it gets matches the drone's state.
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
                    f"command type {command_type.value!r} already has a handler "
                    f"({_name(existing)}); cannot also register {_name(handler)}"
                )
            self._handlers[command_type] = handler
            return handler

        return register

    def command_types(self) -> frozenset[CommandType]:
        return frozenset(self._handlers)

    def dispatch(self, world: World, command: Message[Any], now: float) -> DispatchResult:
        """Run the handler for `command.type` and return its replies.

        Called inside a board transaction, so it never logs, sleeps or publishes. A handler
        that raises, or returns anything but a list of Outgoing, gets an INTERNAL_ERROR NACK
        with a fixed detail instead of propagating, and every change staged in the World is
        discarded (call dispatch once per transaction).

        Steps, in the approved order (design note section 5):
        1. Unknown drone: count it, return no replies.
        2. Link loss: return []. Nothing is applied or cached, not even for a duplicate.
        3. Dedup by msg_id: use the cached replies, apply nothing, go to step 6.
        4. Under REPLY_DELAY with the namespace's delayed replies full: NACK INTERNAL_ERROR
           at once, cached, not applied. Else an injected NACK INJECTED (one NACK_NEXT
           count), else the handler. No handler: NACK UNKNOWN_TYPE.
        5. Cache the ACK or NACK (also an INTERNAL_ERROR one), at most DEDUP_CACHE_SIZE
           per drone.
        6. REPLY_DELAY: stage the replies on the board, due `delay_s` after `now`, and
           return none. So a stacked NACK_NEXT's NACK is delayed too.
        """
        drone = world.drone(command.drone_id)
        if drone is None:
            world.count_unknown_drone_command()
            return DispatchResult(unknown_drone=True)
        if link_lost(drone):
            return DispatchResult(link_lost=True)
        delay_s = reply_delay_s(drone)
        cached = drone.recent_cmds.get(command.msg_id)
        if cached is not None:
            result = DispatchResult(list(cached), duplicate=True)
        elif delay_s is not None and not world.can_delay(namespace_of(command.drone_id)):
            # Bounded memory (invariant 15): refuse the command instead of delaying one more.
            nack = nack_command(command, NackReason.INTERNAL_ERROR, DELAY_FULL_DETAIL)
            _remember(world, command, [nack])
            return DispatchResult([nack])
        else:
            result = self._answer(world, drone, command, now)
            _remember(world, command, result.replies)
        return _maybe_delay(world, result, delay_s, now)

    def _answer(
        self, world: World, drone: Drone, command: Message[Any], now: float
    ) -> DispatchResult:
        """Step 4: an injected NACK (uses up one NACK_NEXT count), or the handler's replies."""
        if take_injected_nack(drone):
            return DispatchResult([nack_command(command, NackReason.INJECTED, INJECTED_DETAIL)])
        return self._run(world, command, now)

    def _run(self, world: World, command: Message[Any], now: float) -> DispatchResult:
        handler = self._handler_for(command)
        if handler is None:
            return DispatchResult(
                [nack_command(command, NackReason.UNKNOWN_TYPE, UNKNOWN_TYPE_DETAIL)]
            )
        try:
            replies = handler(world, command, now)
            if not is_outgoing_list(replies):
                raise TypeError(
                    f"handler returned {type(replies).__name__}, expected list[Outgoing]"
                )
            # Copies, so the publisher never reads live job state.
            return DispatchResult(
                [replace(reply, message=copy.deepcopy(reply.message)) for reply in replies]
            )
        except Exception as exc:
            world.discard()
            nack = nack_command(command, NackReason.INTERNAL_ERROR, INTERNAL_ERROR_DETAIL)
            return DispatchResult([nack], error=exc)

    def _handler_for(self, command: Message[Any]) -> Handler | None:
        try:
            return self._handlers.get(CommandType(command.type.value))
        except ValueError:  # a telemetry type sent as a command
            return None


def _remember(world: World, command: Message[Any], replies: list[Outgoing]) -> None:
    """Cache the command's own reply. Telemetry a handler also returned (a mission's final
    status) is not replayed for a duplicate."""
    drone = world.drone(command.drone_id)  # re-read: a failed handler's discard dropped it
    if drone is not None:
        drone.remember(command.msg_id, [r for r in replies if r.message.type in _REPLY_TYPES])


def _maybe_delay(
    world: World, result: DispatchResult, delay_s: float | None, now: float
) -> DispatchResult:
    """Step 6: under REPLY_DELAY, stage the replies as one-shots due `delay_s` from now and
    return none. Replies that would not fit the namespace's bound go out at once (only a
    duplicate's replay or a reply plus final telemetry can get here past the step-4 check)."""
    if delay_s is None or not result.replies:
        return result
    namespace = namespace_of(result.replies[0].message.drone_id)
    if not world.can_delay(namespace, len(result.replies)):
        return result
    world.delay(now + delay_s, result.replies)
    return replace(result, replies=[], delayed=True)


def _name(handler: Handler) -> str:
    return getattr(handler, "__qualname__", repr(handler))
