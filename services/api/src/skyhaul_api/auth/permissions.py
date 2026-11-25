"""Permissions per role, and the route markers ``@require`` and ``@public`` (ADR 0011).

The markers only label a view. The guard (``skyhaul_api.auth.guard``) reads the label before
every request and refuses a route that has none: default deny.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Final

from skyhaul_fleet_core.models import Role

ACCESS_ATTR: Final = "_skyhaul_access"
PUBLIC: Final = "public"
ANY_USER: Final = "any"


class Permission(StrEnum):
    FLEET_READ = "fleet:read"
    MISSION_READ = "mission:read"
    ALERT_READ = "alert:read"
    COMMAND_SEND = "command:send"
    MISSION_WRITE = "mission:write"
    ALERT_ACK = "alert:ack"
    DRONE_ADMIN = "drone:admin"
    USER_ADMIN = "user:admin"
    AUDIT_READ = "audit:read"


_VIEWER = frozenset({Permission.FLEET_READ, Permission.MISSION_READ, Permission.ALERT_READ})
_DISPATCHER = _VIEWER | {Permission.COMMAND_SEND, Permission.MISSION_WRITE, Permission.ALERT_ACK}
_ADMIN = _DISPATCHER | {Permission.DRONE_ADMIN, Permission.USER_ADMIN, Permission.AUDIT_READ}

ROLE_PERMISSIONS: Final[dict[Role, frozenset[Permission]]] = {
    Role.VIEWER: _VIEWER,
    Role.DISPATCHER: _DISPATCHER,
    Role.ADMIN: _ADMIN,
}


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


def require[F: Callable[..., object]](permission: Permission | None = None) -> Callable[[F], F]:
    """Needs a valid token, and ``permission`` unless it is ``None`` (any signed-in user)."""

    def mark(view: F) -> F:
        setattr(view, ACCESS_ATTR, permission.value if permission else ANY_USER)
        return view

    return mark


def public[F: Callable[..., object]](view: F) -> F:
    """No token needed."""
    setattr(view, ACCESS_ATTR, PUBLIC)
    return view


def access_of(view: Callable[..., object] | None) -> str | None:
    """``public``, ``any``, a permission value, or ``None`` for an unmarked view."""
    value = getattr(view, ACCESS_ATTR, None)
    return value if isinstance(value, str) else None
