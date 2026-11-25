"""The command and mission transition tables allow only the design's states and moves (#24,
#28: late replies after FAILED win, and the mission moves table-driven)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from skyhaul_fleet_core.models import CommandStatus, MissionState
from skyhaul_fleet_core.transitions import COMMAND_TRANSITIONS, MISSION_TRANSITIONS, allowed_from

CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "openapi" / "fleet-api.v1.json"
SCHEMAS = json.loads(CONTRACT.read_text())["components"]["schemas"]

C = CommandStatus
M = MissionState


def test_transition_tables_hold_only_the_design_states_and_terminal_states_absorb() -> None:
    # Exactly the contract's states, each with an entry.
    assert set(COMMAND_TRANSITIONS) == set(SCHEMAS["CommandStatus"]["enum"])
    assert set(MISSION_TRANSITIONS) == set(SCHEMAS["MissionLifecycleState"]["enum"])

    # The moves of m2-design sections 6 and 8, and nothing else.
    assert {s: set(t) for s, t in COMMAND_TRANSITIONS.items()} == {
        C.PENDING: {C.SENT, C.FAILED, C.ACKED, C.NACKED},
        C.SENT: {C.ACKED, C.NACKED, C.TIMED_OUT},
        C.TIMED_OUT: {C.ACKED, C.NACKED},
        C.ACKED: set(),
        C.NACKED: set(),
        C.FAILED: {C.ACKED, C.NACKED},
    }
    assert {s: set(t) for s, t in MISSION_TRANSITIONS.items()} == {
        M.PLANNED: {M.DISPATCHING, M.CANCELLED},
        M.DISPATCHING: {M.ACTIVE, M.FAILED, M.COMPLETED, M.ABORTED},
        M.ACTIVE: {M.COMPLETED, M.ABORTED},
        M.COMPLETED: set(),
        M.ABORTED: set(),
        M.FAILED: set(),
        M.CANCELLED: set(),
    }


# One row per mission event of #28: (state now, the move asked for, allowed).
MISSION_CASES = [
    (M.PLANNED, M.DISPATCHING, True),  # launch
    (M.PLANNED, M.CANCELLED, True),  # cancel
    (M.DISPATCHING, M.ACTIVE, True),  # START_MISSION ACK or MISSION_STATUS ACTIVE
    (M.ACTIVE, M.ACTIVE, False),  # the other of the two arrives second: no-op
    (M.DISPATCHING, M.FAILED, True),  # START_MISSION NACK or the 60 s timeout
    (M.DISPATCHING, M.COMPLETED, True),  # the final status overtook the ACK
    (M.ACTIVE, M.COMPLETED, True),
    (M.ACTIVE, M.ABORTED, True),  # abort, or the automatic return home
    (M.ACTIVE, M.FAILED, False),  # a late NACK never fails a flying mission
    (M.DISPATCHING, M.DISPATCHING, False),  # start twice
    (M.COMPLETED, M.DISPATCHING, False),  # start a completed mission
    (M.COMPLETED, M.ACTIVE, False),  # out-of-order MISSION_STATUS
    (M.FAILED, M.ACTIVE, False),
    (M.ACTIVE, M.CANCELLED, False),  # cancel outside PLANNED
    (M.CANCELLED, M.DISPATCHING, False),
]


@pytest.mark.parametrize(("source", "target", "allowed"), MISSION_CASES)
def test_mission_moves(source: MissionState, target: MissionState, allowed: bool) -> None:
    assert (source in allowed_from(MISSION_TRANSITIONS, target)) is allowed
