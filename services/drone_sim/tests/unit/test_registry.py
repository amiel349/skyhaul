import importlib.util
from pathlib import Path
from typing import Any

import pytest
from sim_support.drones import board_with, make_drone
from sim_support.messages import DRONE, command, outgoing, payload_of, render_minimal
from skyhaul_contracts import CommandType, Message, MessageType
from skyhaul_drone_sim.board import Job, JobsBoard, World
from skyhaul_drone_sim.outbox import Outgoing
from skyhaul_drone_sim.registry import (
    INTERNAL_ERROR_DETAIL,
    DispatchResult,
    DuplicateHandlerError,
    Registry,
)
from skyhaul_drone_sim.replies import ack


def board_with_drone(drone_id: str = DRONE) -> JobsBoard:
    board = board_with()
    board.add_drone(make_drone(drone_id))
    return board


def dispatch(
    registry: Registry, msg: Message[Any], board: JobsBoard | None = None, now: float = 0.0
) -> DispatchResult:
    board = board if board is not None else board_with_drone()
    with board.transaction() as world:
        return registry.dispatch(world, msg, now)


def nack_fields(out: Outgoing) -> dict[str, Any]:
    assert out.message.type is MessageType.NACK
    return payload_of(out)


def test_handler_registered_for_arm_runs_once_when_an_arm_is_dispatched() -> None:
    registry = Registry()
    calls: list[Message[Any]] = []

    @registry.handles(CommandType.ARM)
    def handle_arm(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
        calls.append(msg)
        return [ack(msg)]

    msg = command("ARM")
    result = dispatch(registry, msg)

    assert calls == [msg]
    (reply,) = result.replies
    assert reply.message.type is MessageType.ACK
    assert payload_of(reply) == {"command_msg_id": msg.msg_id, "command_type": "ARM"}
    assert result.error is None


def test_handler_gets_the_world_the_command_and_now() -> None:
    registry = Registry()
    seen: list[tuple[World, Message[Any], float]] = []

    @registry.handles(CommandType.ARM)
    def handle_arm(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
        seen.append((world, msg, now))
        return []

    msg = command("ARM")
    board = board_with_drone()
    with board.transaction() as world:
        registry.dispatch(world, msg, now=42.5)

    assert seen == [(world, msg, 42.5)]


def test_handler_changes_the_board_through_the_world() -> None:
    registry = Registry()
    board = board_with_drone()

    @registry.handles(CommandType.TAKEOFF)
    def handle_takeoff(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
        world.upsert(Job(msg.drone_id, "POSITION", 1.0, now + 1.0, render=render_minimal))
        return []

    dispatch(registry, command("TAKEOFF"), board, now=3.0)

    assert [(job["type"], job["next_due"]) for job in board.snapshot()] == [("POSITION", 4.0)]


def test_two_handlers_for_the_same_type_fail_when_the_module_loads(tmp_path: Path) -> None:
    module_file = tmp_path / "duplicate_handlers.py"
    module_file.write_text(
        "from skyhaul_contracts import CommandType\n"
        "from skyhaul_drone_sim.registry import Registry\n"
        "registry = Registry()\n"
        "@registry.handles(CommandType.ARM)\n"
        "def first(world, command, now):\n"
        "    return []\n"
        "@registry.handles(CommandType.ARM)\n"
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

    @registry.handles(CommandType.ARM)
    def first(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
        return [ack(msg)]

    def second(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
        return []

    with pytest.raises(DuplicateHandlerError):
        registry.handles(CommandType.ARM)(second)

    (reply,) = dispatch(registry, command("ARM")).replies
    assert reply.message.type is MessageType.ACK


def test_raising_handler_returns_an_internal_error_nack_and_the_exception_apart() -> None:
    registry = Registry()
    failure = ValueError("bad altitude 9000")

    @registry.handles(CommandType.GOTO)
    def handle_goto(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
        raise failure

    msg = command("GOTO")
    result = dispatch(registry, msg)

    (reply,) = result.replies
    assert reply.persistent
    assert reply.message.drone_id == DRONE
    assert reply.message.correlation_id == msg.correlation_id
    assert nack_fields(reply) == {
        "command_msg_id": msg.msg_id,
        "command_type": "GOTO",
        "reason": "INTERNAL_ERROR",
        "detail": INTERNAL_ERROR_DETAIL,
    }
    # The exception never reaches the wire, only the consumer's log.
    assert result.error is failure
    assert "9000" not in reply.message.model_dump_json()


def test_registry_keeps_dispatching_after_a_handler_raised() -> None:
    registry = Registry()

    @registry.handles(CommandType.GOTO)
    def handle_goto(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
        raise RuntimeError("bug")

    @registry.handles(CommandType.ARM)
    def handle_arm(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
        return [ack(msg)]

    board = board_with_drone()
    dispatch(registry, command("GOTO"), board)
    (reply,) = dispatch(registry, command("ARM"), board).replies

    assert reply.message.type is MessageType.ACK


def test_type_without_a_handler_returns_an_unknown_type_nack() -> None:
    msg = command("LAND")

    (reply,) = dispatch(Registry(), msg).replies

    assert reply.message.correlation_id == msg.correlation_id
    assert nack_fields(reply) == {
        "command_msg_id": msg.msg_id,
        "command_type": "LAND",
        "reason": "UNKNOWN_TYPE",
        "detail": "no handler for this command type",
    }


def test_telemetry_type_sent_as_a_command_returns_an_unknown_type_nack() -> None:
    registry = Registry()
    heartbeat = outgoing("HEARTBEAT", correlation_id="0b7d4c43-8d33-4f4e-a3e1-5b4e3c1f2a10")

    (reply,) = dispatch(registry, heartbeat.message).replies

    assert nack_fields(reply)["reason"] == "UNKNOWN_TYPE"
    assert nack_fields(reply)["command_type"] == "HEARTBEAT"


def test_command_for_an_unknown_drone_gets_no_reply_and_is_counted() -> None:
    registry = Registry()
    calls: list[Message[Any]] = []

    @registry.handles(CommandType.ARM)
    def handle_arm(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
        calls.append(msg)
        return [ack(msg)]

    board = board_with_drone("main-d001")
    result = dispatch(registry, command("ARM", drone_id="main-d999"), board)
    dispatch(registry, command("ARM", drone_id="main-d999"), board)

    assert result == DispatchResult(replies=[], error=None, unknown_drone=True)
    assert calls == []
    assert board.unknown_drone_commands() == 2


def test_command_types_lists_every_registered_type() -> None:
    registry = Registry()

    @registry.handles(CommandType.ARM)
    def handle_arm(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
        return []

    @registry.handles(CommandType.LAND)
    def handle_land(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
        return []

    assert registry.command_types() == frozenset({CommandType.ARM, CommandType.LAND})


@pytest.mark.parametrize(
    ("returned", "returned_type"),
    [
        (None, "NoneType"),
        ("tuple", "tuple"),
        ([{"ack": True}], "list"),
        ("bare", "Outgoing"),
    ],
    ids=["none", "tuple", "list-of-dicts", "bare-outgoing"],
)
def test_handler_that_returns_anything_but_a_list_of_outgoing_gets_an_internal_error_nack(
    returned: object, returned_type: str
) -> None:
    registry = Registry()

    @registry.handles(CommandType.ARM)
    def handle_arm(world: World, msg: Message[Any], now: float) -> Any:
        if returned == "tuple":
            return (ack(msg),)
        if returned == "bare":
            return ack(msg)
        return returned

    result = dispatch(registry, command("ARM"))

    (reply,) = result.replies
    assert nack_fields(reply)["reason"] == "INTERNAL_ERROR"
    assert nack_fields(reply)["detail"] == INTERNAL_ERROR_DETAIL
    assert isinstance(result.error, TypeError)
    assert str(result.error) == f"handler returned {returned_type}, expected list[Outgoing]"


def test_raising_handler_changes_nothing_on_the_board() -> None:
    registry = Registry()

    @registry.handles(CommandType.ARM)
    def handle_arm(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
        job = world.get(DRONE, "HEARTBEAT")
        assert job is not None
        job.values["armed"] = True
        world.upsert(Job(DRONE, "POSITION", 1.0, 1.0, render=render_minimal))
        world.remove(DRONE, "BATTERY")
        raise RuntimeError("bug after the changes")

    board = board_with_drone()
    board.upsert(Job(DRONE, "HEARTBEAT", 1.0, 1.0, render=render_minimal, values={"armed": False}))
    board.upsert(Job(DRONE, "BATTERY", 5.0, 5.0, render=render_minimal))
    before = board.snapshot()

    (reply,) = dispatch(registry, command("ARM"), board).replies

    assert nack_fields(reply)["reason"] == "INTERNAL_ERROR"
    assert board.snapshot() == before


def test_handler_with_a_bad_return_value_changes_nothing_on_the_board() -> None:
    registry = Registry()

    @registry.handles(CommandType.ARM)
    def handle_arm(world: World, msg: Message[Any], now: float) -> Any:
        job = world.get(DRONE, "HEARTBEAT")
        assert job is not None
        job.values["armed"] = True
        return None

    board = board_with_drone()
    board.upsert(Job(DRONE, "HEARTBEAT", 1.0, 1.0, render=render_minimal, values={"armed": False}))

    dispatch(registry, command("ARM"), board)

    assert board.snapshot()[0]["values"] == {"armed": False}


def test_successful_handler_changes_reach_the_board_when_the_transaction_ends() -> None:
    registry = Registry()

    @registry.handles(CommandType.ARM)
    def handle_arm(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
        job = world.get(DRONE, "HEARTBEAT")
        assert job is not None
        job.values["armed"] = True
        return []

    board = board_with_drone()
    board.upsert(Job(DRONE, "HEARTBEAT", 1.0, 1.0, render=render_minimal, values={"armed": False}))

    dispatch(registry, command("ARM"), board)

    assert board.snapshot()[0]["values"] == {"armed": True}


def test_reply_is_a_copy_not_the_message_object_the_handler_returned() -> None:
    registry = Registry()
    returned: list[Outgoing] = []

    @registry.handles(CommandType.ARM)
    def handle_arm(world: World, msg: Message[Any], now: float) -> list[Outgoing]:
        returned.append(ack(msg))
        return list(returned)

    (reply,) = dispatch(registry, command("ARM")).replies

    assert reply == returned[0]
    assert reply.message is not returned[0].message
