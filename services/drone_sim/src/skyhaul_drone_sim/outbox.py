"""Messages waiting to be published.

The bounded outbox queue and its sentinel arrive with the publisher thread
(second half of #7).
"""

from dataclasses import dataclass
from typing import TypeGuard


@dataclass(frozen=True, slots=True)
class Outgoing:
    """One message the publisher should send.

    Until the contracts package (#6) lands, a message is described by its envelope
    fields plus an opaque payload. The wiring PR replaces `drone_id`, `type`,
    `correlation_id` and `payload` with one `message: Message[BaseModel]`, and #9
    adds `namespace` and `epoch`.
    """

    drone_id: str
    type: str
    correlation_id: str | None
    payload: object
    persistent: bool


def is_outgoing_list(value: object) -> TypeGuard[list[Outgoing]]:
    """True if `value` is a list whose items are all Outgoing (what handlers and ticks return)."""
    return isinstance(value, list) and all(isinstance(item, Outgoing) for item in value)
