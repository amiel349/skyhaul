"""Opening pika connections. Each thread opens its own and is the only one that uses it."""

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Final

import pika
from pika.exceptions import AMQPConnectionError

from skyhaul_drone_sim.config import SimConfig

log = logging.getLogger(__name__)

RETRY_FIRST_S: Final = 0.5
RETRY_MAX_S: Final = 5.0

type Connect = Callable[[float, threading.Event | None], Any]
"""`connect(timeout_s, stop)` opens a new pika `BlockingConnection`, retrying for up to
`timeout_s` (or until `stop` is set), else raises BrokerUnavailable."""


class BrokerUnavailable(RuntimeError):
    """RabbitMQ could not be reached within the configured bound."""


def parameters(cfg: SimConfig, role: str) -> pika.ConnectionParameters:
    """`role` names the connection in the management UI (`skyhaul-drone-sim <role>`)."""
    return pika.ConnectionParameters(
        host=cfg.rabbitmq_host,
        port=cfg.rabbitmq_port,
        virtual_host=cfg.rabbitmq_vhost,
        credentials=pika.PlainCredentials(cfg.rabbitmq_user, cfg.rabbitmq_password),
        heartbeat=cfg.rabbitmq_heartbeat_s,
        connection_attempts=1,
        # A connection the broker keeps blocked (resource alarm) is closed after this, so
        # the publisher reconnects instead of waiting forever.
        blocked_connection_timeout=60,
        client_properties={"connection_name": f"skyhaul-drone-sim {role}"},
    )


def connector(cfg: SimConfig, role: str) -> Connect:
    """The `Connect` function for one role's connections."""
    params = parameters(cfg, role)

    def connect(timeout_s: float, stop: threading.Event | None) -> Any:
        return connect_with_retry(lambda: pika.BlockingConnection(params), role, timeout_s, stop)

    return connect


def connect_with_retry(
    open_connection: Callable[[], Any],
    role: str,
    timeout_s: float,
    stop: threading.Event | None = None,
    *,
    monotonic: Callable[[], float] = time.monotonic,
) -> Any:
    """Call `open_connection` until it succeeds, backing off from 0.5 s to 5 s.

    Raises BrokerUnavailable once `timeout_s` has passed, or right away if `stop` is set
    while waiting. Only connection errors are retried (in pika these include refused
    credentials, which a broker that is still starting can answer with).
    """
    deadline = monotonic() + timeout_s
    delay = RETRY_FIRST_S
    attempt = 0
    while True:
        attempt += 1
        try:
            return open_connection()
        except AMQPConnectionError as exc:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise BrokerUnavailable(
                    f"RabbitMQ unreachable for the {role} after {attempt} attempts "
                    f"({type(exc).__name__})"
                ) from None
            log.warning(
                "RabbitMQ not reachable yet, retrying",
                extra={"role": role, "attempt": attempt, "error": type(exc).__name__},
            )
            waiter = stop if stop is not None else threading.Event()
            if waiter.wait(min(delay, remaining)):
                raise BrokerUnavailable(f"stopped while connecting the {role}") from None
            delay = min(delay * 2, RETRY_MAX_S)


def close_quietly(connection: Any) -> None:
    """Close a connection that may already be broken."""
    if connection is None:
        return
    try:
        if connection.is_open:
            connection.close()
    except Exception:  # noqa: S110 - closing a dead connection has nothing to report
        pass
