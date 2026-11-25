"""The app refuses to boot without a required setting and names it, never its value (#24)."""

from __future__ import annotations

import secrets

import pytest
from skyhaul_api import BOOT_ERROR_EXIT_CODE, create_app

SHORT_SECRET = secrets.token_hex(8)  # random, below the 32-character minimum
URLS = {"DATABASE_URL": "postgresql+psycopg://u@db/x", "REDIS_URL": "redis://redis:6379/0"}


@pytest.mark.parametrize(
    ("env", "named"),
    [
        ({**URLS, "JWT_SECRET": secrets.token_hex(20), "DATABASE_URL": ""}, "DATABASE_URL"),
        ({**URLS}, "JWT_SECRET"),
        ({**URLS, "JWT_SECRET": SHORT_SECRET}, "JWT_SECRET"),
        ({**URLS, "JWT_SECRET": secrets.token_hex(20), "RABBITMQ_HOST": "mq"}, "RABBITMQ_USER"),
    ],
)
def test_boot_exits_with_a_message_naming_the_missing_variable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    env: dict[str, str],
    named: str,
) -> None:
    for name in ("DATABASE_URL", "REDIS_URL", "JWT_SECRET", "RABBITMQ_HOST", "RABBITMQ_USER"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(SystemExit) as exited:
        create_app()

    assert exited.value.code == BOOT_ERROR_EXIT_CODE
    message = capsys.readouterr().err
    assert named in message
    assert SHORT_SECRET not in message
