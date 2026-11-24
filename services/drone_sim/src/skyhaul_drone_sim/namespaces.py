"""Namespaces and namespaced drone ids (ADR 0009).

A namespace matches `^[a-z0-9]{1,16}$`, so it never contains a dash, and a drone id is
`<namespace>-dNNN`. The prefix `namespace + "-"` is therefore unambiguous: `gw1` never
matches `gw10-d001`. Every id is a valid contracts `DroneId`.
"""

import re
from typing import Final

NAMESPACE_PATTERN: Final = r"^[a-z0-9]{1,16}$"
NAMESPACE_RE: Final = re.compile(NAMESPACE_PATTERN)
DRONE_ID_RE: Final = re.compile(r"(?P<namespace>[a-z0-9]{1,16})-d(?P<index>\d{3})")

MAX_DRONES_PER_NAMESPACE: Final = 999
"""`dNNN` has three digits, so a namespace has at most 999 ids between two resets."""


def is_namespace(value: str) -> bool:
    return NAMESPACE_RE.fullmatch(value) is not None


def is_drone_id(value: str) -> bool:
    match = DRONE_ID_RE.fullmatch(value)
    return match is not None and match["index"] != "000"


def nth_drone_id(namespace: str, index: int) -> str:
    """The id of the namespace's `index`-th drone (1-based)."""
    if not is_namespace(namespace):
        raise ValueError("namespace must match ^[a-z0-9]{1,16}$")
    if not 1 <= index <= MAX_DRONES_PER_NAMESPACE:
        raise ValueError(f"drone index must be between 1 and {MAX_DRONES_PER_NAMESPACE}")
    return f"{namespace}-d{index:03}"


def namespace_of(drone_id: str) -> str:
    """The namespace a drone id belongs to: everything before the first dash."""
    return drone_id.partition("-")[0]
