import importlib.util
from pathlib import Path

import pytest
from skyhaul_drone_sim.board import Job, JobsBoard, World
from skyhaul_drone_sim.outbox import Outgoing
from skyhaul_drone_sim.registry import (
    Command,
    DuplicateHandlerError,
    NackPlaceholder,
    Registry,
)


def make_command(type: str = "ARM", msg_id: str = "msg-1") -> Command:
    return Command(
        msg_id=msg_id,
        correlation_id="corr-1",
        drone_id="main-d001",
        type=type,
        payload={},
    )


def ack(command: Command) -> Outgoing:
    return Outgoing(command.drone_id, "ACK", command.correlation_id, {"ok": True}, persistent=True)


def test_handler_registered_for_arm_runs_once_when_an_arm_is_dispatched() -> None:
    registry = Registry()
    calls: list[Command] = []

    @registry.handles("ARM")
    def handle_arm(world: World, command: Command, now: float) -> list[Outgoing]:
        calls.append(command)
        return [ack(command)]

    command = make_command("ARM")
    with JobsBoard().transaction() as world:
        replies = registry.dispatch(world, command, now=1.0)

    assert calls == [command]
    assert replies == [ack(command)]


def test_handler_gets_the_world_the_command_and_now() -> None:
    registry = Registry()
    seen: list[tuple[World, Command, float]] = []

    @registry.handles("ARM")
    def handle_arm(world: World, command: Command, now: float) -> list[Outgoing]:
        seen.append((world, command, now))
        return []

    command = make_command("ARM")
    with JobsBoard().transaction() as world:
        registry.dispatch(world, command, now=42.5)

    assert seen == [(world, command, 42.5)]


def test_handler_changes_the_board_through_the_world() -> None:
    registry = Registry()
    board = JobsBoard()

    @registry.handles("TAKEOFF")
    def handle_takeoff(world: World, command: Command, now: float) -> list[Outgoing]:
        world.upsert(
            Job(command.drone_id, "POSITION", 1.0, now + 1.0, render=lambda job: {}),
        )
        return []

    with board.transaction() as world:
        registry.dispatch(world, make_command("TAKEOFF"), now=3.0)

    assert [(job["type"], job["next_due"]) for job in board.snapshot()] == [("POSITION", 4.0)]


def test_two_handlers_for_the_same_type_fail_when_the_module_loads(tmp_path: Path) -> None:
    module_file = tmp_path / "duplicate_handlers.py"
    module_file.write_text(
        "from skyhaul_drone_sim.registry import Registry\n"
        "registry = Registry()\n"
        "@registry.handles('ARM')\n"
        "def first(world, command, now):\n"
        "    return []\n"
        "@registry.handles('ARM')\n"
        "def second(world, command, now):\n"
        "    return []\n"
    )
    spec = importlib.util.spec_from_file_location("duplicate_handlers", module_file)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    with pytest.raises(DuplicateHandlerError, match=r"'ARM' already has a handler \(first\)"):
        spec.loader.exec_module(module)


def test_failed_duplicate_registration_keeps_the_first_handler() -> None:
    registry = Registry()

    @registry.handles("ARM")
    def first(world: World, command: Command, now: float) -> list[Outgoing]:
        return [ack(command)]

    def second(world: World, command: Command, now: float) -> list[Outgoing]:
        return []

    with pytest.raises(DuplicateHandlerError):
        registry.handles("ARM")(second)

    command = make_command("ARM")
    with JobsBoard().transaction() as world:
        assert registry.dispatch(world, command, now=0.0) == [ack(command)]


def test_raising_handler_returns_an_internal_error_nack_instead_of_propagating() -> None:
    registry = Registry()
    failure = ValueError("bad altitude 9000")

    @registry.handles("GOTO")
    def handle_goto(world: World, command: Command, now: float) -> list[Outgoing]:
        raise failure

    command = make_command("GOTO", msg_id="msg-7")
    with JobsBoard().transaction() as world:
        (reply,) = registry.dispatch(world, command, now=0.0)

    assert reply == Outgoing(
        drone_id="main-d001",
        type="NACK",
        correlation_id="corr-1",
        payload=NackPlaceholder(
            command_msg_id="msg-7",
            command_type="GOTO",
            reason="INTERNAL_ERROR",
            detail="handler raised ValueError",
            error=failure,
        ),
        persistent=True,
    )


def test_registry_keeps_dispatching_after_a_handler_raised() -> None:
    registry = Registry()

    @registry.handles("GOTO")
    def handle_goto(world: World, command: Command, now: float) -> list[Outgoing]:
        raise RuntimeError("bug")

    @registry.handles("ARM")
    def handle_arm(world: World, command: Command, now: float) -> list[Outgoing]:
        return [ack(command)]

    board = JobsBoard()
    with board.transaction() as world:
        registry.dispatch(world, make_command("GOTO"), now=0.0)
    arm = make_command("ARM", msg_id="msg-2")
    with board.transaction() as world:
        replies = registry.dispatch(world, arm, now=0.0)

    assert replies == [ack(arm)]


def test_type_without_a_handler_returns_an_unknown_type_nack() -> None:
    registry = Registry()

    command = make_command("FLIP" * 20)
    with JobsBoard().transaction() as world:
        (reply,) = registry.dispatch(world, command, now=0.0)

    assert reply.type == "NACK"
    assert reply.correlation_id == "corr-1"
    assert reply.payload == NackPlaceholder(
        command_msg_id="msg-1",
        command_type=("FLIP" * 20)[:32],
        reason="UNKNOWN_TYPE",
        detail="no handler for this command type",
    )


def test_command_types_lists_every_registered_type() -> None:
    registry = Registry()

    @registry.handles("ARM")
    def handle_arm(world: World, command: Command, now: float) -> list[Outgoing]:
        return []

    @registry.handles("LAND")
    def handle_land(world: World, command: Command, now: float) -> list[Outgoing]:
        return []

    assert registry.command_types() == frozenset({"ARM", "LAND"})
