"""Command and mission state machines (m2-design sections 6 and 8) and the CAS update.

Every state change goes through ``cas_update``: ``UPDATE ... WHERE id = :id AND state IN
(allowed sources)``. A duplicate or out-of-order message then changes nothing, so a row
never moves backwards and terminal states absorb.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final

from sqlalchemy import update
from sqlalchemy.orm import Session

from skyhaul_fleet_core.models import Command, CommandStatus, Mission, MissionState

S = CommandStatus
COMMAND_TRANSITIONS: Final[Mapping[CommandStatus, frozenset[CommandStatus]]] = MappingProxyType(
    {
        # A reply can overtake the broker confirm, so PENDING may go straight to a reply.
        S.PENDING: frozenset({S.SENT, S.FAILED, S.ACKED, S.NACKED}),
        S.SENT: frozenset({S.ACKED, S.NACKED, S.TIMED_OUT}),
        # Late replies win (they also set late=true).
        S.TIMED_OUT: frozenset({S.ACKED, S.NACKED}),
        S.ACKED: frozenset(),
        S.NACKED: frozenset(),
        S.FAILED: frozenset(),
    }
)

M = MissionState
MISSION_TRANSITIONS: Final[Mapping[MissionState, frozenset[MissionState]]] = MappingProxyType(
    {
        M.PLANNED: frozenset({M.DISPATCHING, M.CANCELLED}),
        # The final status may overtake the START_MISSION ACK.
        M.DISPATCHING: frozenset({M.ACTIVE, M.FAILED, M.COMPLETED, M.ABORTED}),
        M.ACTIVE: frozenset({M.COMPLETED, M.ABORTED}),
        M.COMPLETED: frozenset(),
        M.ABORTED: frozenset(),
        M.FAILED: frozenset(),
        M.CANCELLED: frozenset(),
    }
)


def allowed_from[E: StrEnum](table: Mapping[E, frozenset[E]], target: E) -> frozenset[E]:
    """The states a row may be in to move to ``target``."""
    return frozenset(source for source, targets in table.items() if target in targets)


def cas_update(
    session: Session,
    model: type[Command] | type[Mission],
    row_id: uuid.UUID,
    target: CommandStatus | MissionState,
    **values: Any,
) -> bool:
    """Move one row to ``target`` if its current state allows it. True if the row moved.

    ``values`` are extra columns to set in the same statement (``sent_at``, ``late``, ...).
    The caller owns the transaction.
    """
    if model is Command:
        if not isinstance(target, CommandStatus):
            raise TypeError("a command target must be a CommandStatus")
        moved = (
            update(Command)
            .where(
                Command.id == row_id, Command.status.in_(allowed_from(COMMAND_TRANSITIONS, target))
            )
            .values(status=target, **values)
        )
    else:
        if not isinstance(target, MissionState):
            raise TypeError("a mission target must be a MissionState")
        moved = (
            update(Mission)
            .where(
                Mission.id == row_id, Mission.state.in_(allowed_from(MISSION_TRANSITIONS, target))
            )
            .values(state=target, **values)
        )
    return session.execute(moved.returning(model.id)).first() is not None
