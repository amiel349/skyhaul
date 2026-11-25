"""Auth without a database (#25): argon2id, JWT validation, the permission matrix as declared,
default deny, no token is 401, and a dead Redis makes login fail closed."""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from argon2 import extract_parameters
from argon2.profiles import RFC_9106_LOW_MEMORY
from flask import Flask
from flask.testing import FlaskClient
from skyhaul_api.auth.passwords import hash_password, verify_password
from skyhaul_api.auth.permissions import (
    ANY_USER,
    PUBLIC,
    ROLE_PERMISSIONS,
    Permission,
    access_of,
)
from skyhaul_api.auth.tokens import (
    AUDIENCE,
    ISSUER,
    TOKEN_TTL,
    InvalidTokenError,
    issue_token,
    verify_token,
)
from skyhaul_api.openapi import API_PREFIX
from skyhaul_fleet_core.models import Role

SECRET = secrets.token_hex(32)
CONVERTER = re.compile(r"<(?:[^<>:]+:)?([^<>]+)>")


def test_passwords_are_argon2id_with_the_rfc_9106_low_memory_profile() -> None:
    stored = hash_password("correct horse battery")
    assert stored.startswith("$argon2id$")
    params = extract_parameters(stored)
    assert (params.memory_cost, params.time_cost, params.parallelism) == (
        RFC_9106_LOW_MEMORY.memory_cost,
        RFC_9106_LOW_MEMORY.time_cost,
        RFC_9106_LOW_MEMORY.parallelism,
    )
    assert verify_password(stored, "correct horse battery")
    assert not verify_password(stored, "wrong")


def _claims(**changes: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(uuid.uuid4()),
        "role": "admin",
        "iat": now,
        "exp": now + TOKEN_TTL,
        "jti": str(uuid.uuid4()),
    }
    claims.update(changes)
    return {k: v for k, v in claims.items() if v is not None}


def test_a_token_lasts_15_minutes_and_carries_every_claim() -> None:
    user_id = uuid.uuid4()
    token = issue_token(user_id, "viewer", SECRET)
    claims = jwt.decode(token, SECRET, algorithms=["HS256"], audience=AUDIENCE)
    assert set(claims) == {"iss", "aud", "sub", "role", "iat", "exp", "jti"}
    assert claims["exp"] - claims["iat"] == 15 * 60
    assert verify_token(token, SECRET) == user_id


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(
            jwt.encode(_claims(exp=datetime.now(UTC) - timedelta(seconds=1)), SECRET), id="expired"
        ),
        pytest.param(jwt.encode(_claims(iss="someone-else"), SECRET), id="wrong-iss"),
        pytest.param(jwt.encode(_claims(aud="other"), SECRET), id="wrong-aud"),
        pytest.param(jwt.encode(_claims(jti=None), SECRET), id="no-jti"),
        pytest.param(jwt.encode(_claims(), secrets.token_hex(32)), id="other-key"),
        pytest.param(jwt.encode(_claims(), None, algorithm="none"), id="alg-none"),
        pytest.param(issue_token(uuid.uuid4(), "admin", SECRET)[:-3] + "AAA", id="tampered"),
    ],
)
def test_bad_tokens_are_refused(token: str) -> None:
    with pytest.raises(InvalidTokenError):
        verify_token(token, SECRET)


def test_role_permissions_follow_the_design() -> None:
    viewer = {Permission.FLEET_READ, Permission.MISSION_READ, Permission.ALERT_READ}
    dispatcher = viewer | {Permission.COMMAND_SEND, Permission.MISSION_WRITE, Permission.ALERT_ACK}
    assert ROLE_PERMISSIONS[Role.VIEWER] == viewer
    assert ROLE_PERMISSIONS[Role.DISPATCHER] == dispatcher
    assert ROLE_PERMISSIONS[Role.ADMIN] == set(Permission)


def _api_routes(app: Flask) -> list[tuple[str, str, str]]:
    """(method, contract path, endpoint) of every API route."""
    routes = []
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith(f"{API_PREFIX}/"):
            continue
        path = CONVERTER.sub(r"{\1}", rule.rule.removeprefix(API_PREFIX))
        for method in sorted((rule.methods or set()) - {"HEAD", "OPTIONS"}):
            routes.append((method, path, rule.endpoint))
    return routes


def test_every_route_is_marked_and_matches_the_contract_permission(
    app: Flask, contract: dict[str, Any]
) -> None:
    """The route walk: a route missing from the contract's permission matrix fails here."""
    unmarked = [
        rule.rule
        for rule in app.url_map.iter_rules()
        if access_of(app.view_functions[rule.endpoint]) is None
    ]
    assert unmarked == ["/static/<path:filename>"] or unmarked == []
    for method, path, endpoint in _api_routes(app):
        if path.startswith("/_test/"):
            continue
        operation = contract["paths"][path][method.lower()]
        expected = PUBLIC if operation.get("security") == [] else operation["x-permission"]
        assert access_of(app.view_functions[endpoint]) == expected, (method, path)


def test_a_route_without_a_marker_is_refused(app: Flask) -> None:
    app.add_url_rule("/api/v1/_test/unmarked", "unmarked", lambda: "reachable")
    response = app.test_client().get("/api/v1/_test/unmarked")
    assert response.status_code == 403
    assert "reachable" not in response.get_data(as_text=True)


def test_every_protected_route_without_a_token_is_401(app: Flask, client: FlaskClient) -> None:
    protected = [
        (m, p) for m, p, e in _api_routes(app) if access_of(app.view_functions[e]) != PUBLIC
    ]
    assert protected
    for method, path in protected:
        url = API_PREFIX + path.replace("{user_id}", str(uuid.uuid4())).replace("{drone_id}", "d-1")
        for headers in ({}, {"Authorization": "Bearer not-a-token"}, {"Authorization": "Basic x"}):
            response = client.open(url, method=method, headers=headers)
            assert response.status_code == 401, (method, path, headers)
            assert response.get_json()["error"] == "unauthorized"
    assert ANY_USER in {access_of(app.view_functions[e]) for _, _, e in _api_routes(app)}


def test_login_fails_closed_with_503_when_redis_is_down(client: FlaskClient) -> None:
    """The unit app's Redis is unreachable: the limiter refuses instead of letting it through."""
    response = client.post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": "secret-password"}
    )
    assert response.status_code == 503
    assert response.get_json()["error"] == "rate_limit_unavailable"
    assert "secret-password" not in response.get_data(as_text=True)
