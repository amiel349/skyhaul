"""Read settings from environment variables. Fail fast; never default a secret.

Error messages name the variable, never its value.
"""

from __future__ import annotations

import os


class SettingsError(RuntimeError):
    """A required setting is missing or invalid. The message never contains the value."""


def require(name: str) -> str:
    """The value of a required variable. Missing or empty is an error."""
    value = os.environ.get(name, "")
    if not value.strip():
        raise SettingsError(f"{name} is not set")
    return value


def require_secret(name: str, *, min_length: int = 32) -> str:
    """A required secret of at least ``min_length`` characters."""
    value = require(name)
    if len(value) < min_length:
        raise SettingsError(f"{name} must be at least {min_length} characters")
    return value


def optional_int(name: str, default: int) -> int:
    """An integer setting with a default. Only for non-secret tuning values."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise SettingsError(f"{name} must be an integer") from None
