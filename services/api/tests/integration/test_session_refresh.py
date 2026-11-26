"""Session refresh against real Postgres and Redis (#75, ADR 0018). One test per acceptance
criterion. The clients keep no cookie jar: each request sends its cookie explicitly."""

from __future__ import annotations

import re
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from skyhaul_fleet_core.models import AuditEntry, RefreshToken, Role, User
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

pytestmark = pytest.mark.integration

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"

MakeUser = Callable[[Role], User]


class Login:
    """The refresh cookie and CSRF token of a login or refresh response."""

    def __init__(self, response: TestResponse) -> None:
        assert response.status_code == 200, response.get_json()
        self.token = cookie(response, "skyhaul_refresh")
        self.csrf: str = response.get_json()["csrf_token"]


SignIn = Callable[[User], Login]


def set_cookie_header(response: TestResponse, name: str) -> str:
    return next(h for h in response.headers.getlist("Set-Cookie") if h.startswith(f"{name}="))


def cookie(response: TestResponse, name: str) -> str:
    found = re.match(rf"{name}=([^;]*)", set_cookie_header(response, name))
    assert found
    return found.group(1)


@pytest.fixture
def client(api_app: Flask) -> FlaskClient:
    return api_app.test_client(use_cookies=False)


@pytest.fixture
def login(client: FlaskClient, password: str) -> SignIn:
    def sign_in(user: User) -> Login:
        return Login(client.post(LOGIN, json={"email": user.email, "password": password}))

    return sign_in


def post(client: FlaskClient, url: str, token: str | None, csrf: str | None) -> TestResponse:
    headers = {} if csrf is None else {"X-CSRF-Token": csrf}
    if token is not None:
        headers["Cookie"] = f"skyhaul_refresh={token}"
    return client.post(url, headers=headers)


def rows_of(session: Session, user: User) -> list[RefreshToken]:
    session.expire_all()
    return list(session.scalars(select(RefreshToken).where(RefreshToken.user_id == user.id)))


def last_audit_id(session: Session) -> int:
    return int(session.scalar(select(func.coalesce(func.max(AuditEntry.id), 0))) or 0)


def audit_since(session: Session, first_id: int) -> list[AuditEntry]:
    session.expire_all()
    return list(session.scalars(select(AuditEntry).where(AuditEntry.id > first_id)))


def assert_cleared(response: TestResponse) -> None:
    for name in ("skyhaul_refresh", "skyhaul_csrf"):
        header = set_cookie_header(response, name)
        assert header.startswith(f"{name}=;")
        assert "Max-Age=0" in header


def test_login_sets_the_refresh_and_csrf_cookies_and_returns_csrf_token(
    client: FlaskClient, password: str, make_user: MakeUser
) -> None:
    user = make_user(Role.VIEWER)
    response = client.post(LOGIN, json={"email": user.email, "password": password})
    body = response.get_json()
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", body["csrf_token"])
    refresh = set_cookie_header(response, "skyhaul_refresh").split("; ")
    csrf = set_cookie_header(response, "skyhaul_csrf").split("; ")
    for flag in ("HttpOnly", "SameSite=Strict", "Path=/api/v1/auth", "Secure", "Max-Age=604800"):
        assert flag in refresh
    for flag in ("SameSite=Strict", "Path=/", "Secure", "Max-Age=604800"):
        assert flag in csrf
    assert "HttpOnly" not in csrf
    assert cookie(response, "skyhaul_csrf") == body["csrf_token"]
    assert cookie(response, "skyhaul_refresh") not in response.get_data(as_text=True)


def test_refresh_returns_a_new_access_token_and_rotates_the_cookie(
    client: FlaskClient, login: SignIn, make_user: MakeUser
) -> None:
    user = make_user(Role.DISPATCHER)
    first = login(user)
    response = post(client, REFRESH, first.token, first.csrf)
    body = response.get_json()
    assert response.status_code == 200
    assert set(body) == {"access_token", "token_type", "expires_in", "user", "csrf_token"}
    assert body["user"]["id"] == str(user.id)
    assert cookie(response, "skyhaul_refresh") != first.token
    assert body["csrf_token"] != first.csrf
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    # The old token is refused: within the grace window with 409, issuing nothing.
    again = post(client, REFRESH, first.token, first.csrf)
    assert again.status_code == 409
    assert again.get_json()["error"] == "refresh_superseded"
    assert "Set-Cookie" not in again.headers


def test_reuse_after_the_grace_window_revokes_the_family(
    client: FlaskClient, login: SignIn, make_user: MakeUser, db_session: Session
) -> None:
    user = make_user(Role.VIEWER)
    first = login(user)
    current = Login(post(client, REFRESH, first.token, first.csrf))
    db_session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.used_at.is_not(None))
        .values(used_at=datetime.now(UTC) - timedelta(seconds=11))
    )
    db_session.commit()
    mark = last_audit_id(db_session)

    replay = post(client, REFRESH, first.token, first.csrf)
    assert replay.status_code == 401
    assert replay.get_json()["error"] == "session_invalid"
    assert all(row.revoked_at is not None for row in rows_of(db_session, user))
    assert post(client, REFRESH, current.token, current.csrf).status_code == 401
    reuse = [e for e in audit_since(db_session, mark) if e.action == "auth.refresh.reuse"]
    assert [(e.outcome, e.actor_user_id) for e in reuse] == [("denied", user.id)]


def test_missing_or_wrong_csrf_is_403_and_rotates_nothing(
    client: FlaskClient, login: SignIn, make_user: MakeUser, db_session: Session
) -> None:
    user = make_user(Role.VIEWER)
    session = login(user)
    for csrf in (None, "x" * 43):
        response = post(client, REFRESH, session.token, csrf)
        assert response.status_code == 403
        assert response.get_json()["error"] == "csrf_failed"
    rows = rows_of(db_session, user)
    assert len(rows) == 1
    assert rows[0].used_at is None
    assert post(client, REFRESH, session.token, session.csrf).status_code == 200


@pytest.mark.parametrize("case", ["unknown", "expired", "revoked", "inactive", "no-cookie"])
def test_a_dead_cookie_or_an_inactive_user_is_401_and_clears_the_cookies(
    case: str, client: FlaskClient, login: SignIn, make_user: MakeUser, db_session: Session
) -> None:
    user = make_user(Role.VIEWER)
    session = login(user)
    token: str | None = {"unknown": uuid.uuid4().hex, "no-cookie": None}.get(case, session.token)
    changes: dict[str, dict[str, Any]] = {
        "expired": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)},
        "revoked": {"revoked_at": datetime.now(UTC)},
    }
    if case in changes:
        db_session.execute(
            update(RefreshToken).where(RefreshToken.user_id == user.id).values(**changes[case])
        )
    if case == "inactive":
        db_session.execute(update(User).where(User.id == user.id).values(is_active=False))
    db_session.commit()

    response = post(client, REFRESH, token, session.csrf)
    assert response.status_code == 401
    assert response.get_json()["error"] == "session_invalid"
    assert_cleared(response)
    if case == "inactive":
        assert all(row.revoked_at is not None for row in rows_of(db_session, user))


def test_logout_revokes_the_family_and_is_idempotent(
    client: FlaskClient, login: SignIn, make_user: MakeUser, db_session: Session
) -> None:
    user = make_user(Role.VIEWER)
    session = login(user)
    assert post(client, LOGOUT, session.token, "y" * 43).status_code == 403
    assert rows_of(db_session, user)[0].revoked_at is None

    first = post(client, LOGOUT, session.token, session.csrf)
    assert first.status_code == 204
    assert_cleared(first)
    assert rows_of(db_session, user)[0].revoked_at is not None
    assert post(client, REFRESH, session.token, session.csrf).status_code == 401
    for token in (session.token, None):
        again = post(client, LOGOUT, token, session.csrf)
        assert again.status_code == 204
        assert_cleared(again)


def test_refresh_and_logout_are_rate_limited_per_ip_without_a_bearer(client: FlaskClient) -> None:
    env = {"REMOTE_ADDR": "198.51.100.30"}
    for url, status in ((REFRESH, 401), (LOGOUT, 204)):
        statuses = [client.post(url, environ_base=env).status_code for _ in range(31)]
        assert statuses[:30] == [status] * 30
        assert statuses[30] == 429


def test_audit_entries_never_hold_tokens_csrf_or_cookie_headers(
    client: FlaskClient, login: SignIn, make_user: MakeUser, db_session: Session
) -> None:
    mark = last_audit_id(db_session)
    first = login(make_user(Role.VIEWER))
    post(client, REFRESH, first.token, "z" * 43)
    second = Login(post(client, REFRESH, first.token, first.csrf))
    post(client, LOGOUT, second.token, second.csrf)
    post(client, REFRESH, second.token, second.csrf)

    entries = audit_since(db_session, mark)
    assert {e.action for e in entries} >= {"auth.login", "auth.refresh", "auth.logout"}
    text = repr([(e.action, e.target_id, e.data) for e in entries])
    for secret in (first.token, first.csrf, second.token, second.csrf, "z" * 43):
        assert secret not in text
    assert "skyhaul_refresh" not in text
    assert "Cookie" not in text


def test_two_concurrent_refreshes_give_one_200_and_one_409(
    api_app: Flask, login: SignIn, make_user: MakeUser
) -> None:
    session = login(make_user(Role.VIEWER))
    barrier = threading.Barrier(2)
    statuses: list[int] = []

    def refresh() -> None:
        client = api_app.test_client(use_cookies=False)
        barrier.wait()
        statuses.append(post(client, REFRESH, session.token, session.csrf).status_code)

    threads = [threading.Thread(target=refresh) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert sorted(statuses) == [200, 409]
