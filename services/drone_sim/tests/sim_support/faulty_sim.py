"""The real simulator process, with a handler that raises for LAND (tests only).

Run as `python -m sim_support.faulty_sim`: same main thread, signals and threads as
`python -m skyhaul_drone_sim`, but the registry has ARM -> ACK and LAND -> raise.
"""

from typing import Any

from skyhaul_contracts import CommandType, Message
from skyhaul_drone_sim.__main__ import main
from skyhaul_drone_sim.board import World
from skyhaul_drone_sim.outbox import Outgoing
from skyhaul_drone_sim.registry import Registry
from skyhaul_drone_sim.replies import ack

registry = Registry()


@registry.handles(CommandType.ARM)
def handle_arm(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
    return [ack(msg)]


@registry.handles(CommandType.LAND)
def handle_land(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
    raise RuntimeError("deliberate handler bug for the integration test")


if __name__ == "__main__":
    raise SystemExit(main(registry))
