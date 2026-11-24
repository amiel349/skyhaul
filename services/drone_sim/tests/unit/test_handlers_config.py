"""The real handler registry, the environment config and the bounded broker retry."""

import threading

import pytest
from pika.exceptions import AMQPConnectionError
from sim_support.drones import board_with
from sim_support.messages import DRONE, command, payload_of
from skyhaul_contracts import CommandType, MessageType
from skyhaul_drone_sim.amqp import BrokerUnavailable, connect_with_retry
from skyhaul_drone_sim.config import ConfigError, SimConfig
from skyhaul_drone_sim.handlers import registry

ENV = {"RABBITMQ_HOST": "rabbitmq", "RABBITMQ_USER": "sim", "RABBITMQ_PASSWORD": "pw"}


def test_every_command_type_has_a_handler() -> None:
    assert registry.command_types() == frozenset(CommandType)


@pytest.mark.parametrize("type", list(CommandType))
def test_every_command_type_gets_an_ack_or_an_explicit_nack(type: CommandType) -> None:
    board = board_with(DRONE)
    msg = command(type)

    with board.transaction() as world:
        result = registry.dispatch(world, msg, 0.0)

    (reply,) = result.replies
    assert result.error is None
    assert reply.message.correlation_id == msg.correlation_id
    assert payload_of(reply)["command_msg_id"] == msg.msg_id
    if type in (CommandType.ARM, CommandType.SET_SPEED):  # what a landed drone accepts
        assert reply.message.type is MessageType.ACK
    else:
        assert reply.message.type is MessageType.NACK
        assert payload_of(reply)["reason"] == "INVALID_STATE"


def test_config_from_env_uses_defaults_for_everything_but_secrets() -> None:
    cfg = SimConfig.from_env({**ENV, "SIM_DRONES": "main-d001, main-d002,main-d001"})

    assert (cfg.rabbitmq_port, cfg.tick_s, cfg.outbox_max, cfg.prefetch) == (5672, 0.1, 1000, 32)
    assert cfg.drones == ("main-d001", "main-d002")
    assert "pw" not in repr(cfg)


@pytest.mark.parametrize("missing", ["RABBITMQ_HOST", "RABBITMQ_USER", "RABBITMQ_PASSWORD"])
def test_missing_required_variable_fails_fast_naming_it(missing: str) -> None:
    env = {k: v for k, v in ENV.items() if k != missing}

    with pytest.raises(ConfigError, match=f"{missing} must be set"):
        SimConfig.from_env(env)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("RABBITMQ_PORT", "abc"),
        ("RABBITMQ_PORT", "70000"),
        ("SIM_OUTBOX_MAX", "-7"),
        ("SIM_TICK_S", "nan"),
        ("SIM_DRAIN_S", "9"),
        ("SIM_DEBUG_TAP", "maybe"),
        ("SIM_DRONES", "Main.D1"),
    ],
)
def test_invalid_values_fail_fast_without_echoing_them(name: str, value: str) -> None:
    with pytest.raises(ConfigError, match=name) as info:
        SimConfig.from_env({**ENV, name: value})

    assert value not in str(info.value)


def test_unreachable_broker_is_retried_with_a_bound_then_reported() -> None:
    attempts = 0
    now = 0.0

    def refuse() -> object:
        nonlocal attempts
        attempts += 1
        raise AMQPConnectionError("refused")

    def clock() -> float:
        return now

    class AdvancingStop(threading.Event):
        def wait(self, timeout: float | None = None) -> bool:
            nonlocal now
            now += timeout or 0.0
            return False

    with pytest.raises(BrokerUnavailable, match="unreachable for the consumer after 6 attempts"):
        connect_with_retry(refuse, "consumer", 8.0, AdvancingStop(), monotonic=clock)
    assert attempts == 6  # waits 0.5, 1, 2, 4, then the last 0.5 s up to the 8 s bound


def test_stop_while_retrying_gives_up_at_once() -> None:
    stop = threading.Event()
    stop.set()

    def refuse() -> object:
        raise AMQPConnectionError("refused")

    with pytest.raises(BrokerUnavailable, match="stopped"):
        connect_with_retry(refuse, "publisher", 60.0, stop)
