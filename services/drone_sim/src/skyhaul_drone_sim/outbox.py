"""Messages waiting to be published, and the bounded outbox queue between producers and the
publisher thread.

The consumer and the scheduler put; only the publisher gets. The queue is bounded, so when
the publisher falls behind (or the broker is gone) the producers block and RabbitMQ holds
the unhandled commands (backpressure). Nothing is ever put while a board or codec lock is
held (design note invariant 8).
"""

import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Final, TypeGuard

from pydantic import BaseModel
from skyhaul_contracts import Codec, Message, MessageType

PUT_POLL_S: Final = 0.5
"""How long one blocking put waits before checking the stop event again."""


UNSTAMPED: Final = -1
"""The epoch of a message nobody stamped yet. It is never current, so it is never sent."""


@dataclass(frozen=True, slots=True)
class Outgoing:
    """One message the publisher should send.

    `namespace` and `epoch` say which namespace epoch produced it (ADR 0009). The board
    stamps them under its lock (`JobsBoard.stamp`, `World.stamp`, `due`), and the publisher
    drops a message whose epoch is no longer current. Unstamped messages are dropped too.
    """

    message: Message[BaseModel]
    persistent: bool
    namespace: str = ""
    epoch: int = UNSTAMPED


class Sentinel(Enum):
    """Put once, after every producer has stopped: the publisher drains up to it and exits."""

    SENTINEL = "SENTINEL"


SENTINEL: Final = Sentinel.SENTINEL

type Outbox = queue.Queue[Outgoing | Sentinel]


def new_outbox(maxsize: int) -> Outbox:
    if maxsize <= 0:
        raise ValueError("the outbox must be bounded")
    return queue.Queue(maxsize=maxsize)


def put_until_stopped(outbox: Outbox, item: Outgoing, stop: threading.Event) -> bool:
    """Block until `item` is in the outbox. Give up only once `stop` is set and the outbox
    stays full for another poll interval. Returns True if the item was put."""
    while True:
        try:
            outbox.put(item, timeout=PUT_POLL_S)
            return True
        except queue.Full:
            if stop.is_set():
                return False


# `Codec.new` only builds and validates a `Message`: it takes no lock and counts nothing, so
# it is safe inside a board transaction. Encoding happens later, in the publisher.
_BUILDER: Final = Codec()


def new_message(
    type: MessageType,
    drone_id: str,
    payload: BaseModel,
    correlation_id: str | None,
    *,
    ts: datetime | None = None,
) -> Message[BaseModel]:
    """A validated message with a fresh msg_id. Raises EncodeError if a field is wrong."""
    return _BUILDER.new(type, drone_id, payload, correlation_id, ts=ts)


def is_outgoing_list(value: object) -> TypeGuard[list[Outgoing]]:
    """True if `value` is a list whose items are all Outgoing (what handlers and ticks return)."""
    return isinstance(value, list) and all(isinstance(item, Outgoing) for item in value)
