"""The command and mission transition tables allow only the design's states and moves (#24)."""

from __future__ import annotations

import json
from pathlib import Path

from skyhaul_fleet_core.models import CommandStatus, MissionState
from skyhaul_fleet_core.transitions import COMMAND_TRANSITIONS, MISSION_TRANSITIONS

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
        C.FAILED: set(),
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
