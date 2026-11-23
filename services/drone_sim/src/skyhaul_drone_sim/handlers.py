"""One handler per `CommandType`.

PLACEHOLDERS until the drone behavior issue (#8): ARM is acknowledged without changing any
state, and every other command is refused with NACK `INTERNAL_ERROR` and a fixed detail, so
no sender mistakes "not implemented yet" for "accepted". #8 replaces each of them with the
real state machine.
"""

from typing import Any, Final

from skyhaul_contracts import CommandType, Message, NackReason

from skyhaul_drone_sim.board import World
from skyhaul_drone_sim.outbox import Outgoing
from skyhaul_drone_sim.registry import Registry
from skyhaul_drone_sim.replies import ack, nack_command

NOT_IMPLEMENTED_DETAIL: Final = "command not implemented by this simulator version"

registry = Registry()


@registry.handles(CommandType.ARM)
def handle_arm(world: World, command: Message[Any], now: float) -> list[Outgoing]:
    """PLACEHOLDER (#8): accept the command, change nothing."""
    return [ack(command)]


def _not_implemented(world: World, command: Message[Any], now: float) -> list[Outgoing]:
    """PLACEHOLDER (#8): refuse the command."""
    return [nack_command(command, NackReason.INTERNAL_ERROR, NOT_IMPLEMENTED_DETAIL)]


for _type in CommandType:
    if _type is not CommandType.ARM:
        registry.handles(_type)(_not_implemented)
