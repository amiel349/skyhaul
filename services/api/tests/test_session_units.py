"""Session refresh without a database (#75): token generation, hashing, ``refresh_ttl`` and
the cookie flags."""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta

import pytest
from skyhaul_api.auth.cookies import CSRF_COOKIE, REFRESH_COOKIE, cookie_settings
from skyhaul_api.auth.sessions import digest, new_secret, refresh_ttl
from skyhaul_api.config import Config
from skyhaul_fleet_core.models import Role

BASE64URL_43 = re.compile(r"^[A-Za-z0-9_-]{43}$")


def test_tokens_are_43_base64url_characters_unique_and_stored_as_sha256() -> None:
    tokens = {new_secret() for _ in range(50)}
    assert len(tokens) == 50
    assert all(BASE64URL_43.match(token) for token in tokens)
    token = tokens.pop()
    assert digest(token) == hashlib.sha256(token.encode()).digest()
    assert len(digest(token)) == 32


def test_refresh_ttl_is_the_configured_lifetime_for_every_role() -> None:
    config = Config("db", "redis", "x" * 32, "mq", "user", "pw", refresh_token_ttl_s=3600)
    assert {refresh_ttl(role, config) for role in Role} == {timedelta(hours=1)}


@pytest.mark.parametrize("secure", [True, False])
def test_cookie_flags(secure: bool) -> None:
    cookies = {name: options for name, _, options in cookie_settings("t", "c", 60, secure=secure)}
    assert cookies[REFRESH_COOKIE] == {
        "max_age": 60,
        "secure": secure,
        "samesite": "Strict",
        "httponly": True,
        "path": "/api/v1/auth",
    }
    assert cookies[CSRF_COOKIE] == {
        "max_age": 60,
        "secure": secure,
        "samesite": "Strict",
        "httponly": False,
        "path": "/",
    }
