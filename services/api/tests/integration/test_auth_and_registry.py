"""Auth, roles, users, audit and the drone registry against real Postgres and Redis (#25).
One behavior test per acceptance criterion."""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from collections.abc import Callable
from typing import Any

import jwt
import pytest
from flask import Flask
from flask.testing import FlaskClient
from psycopg.errors import CheckViolation, UniqueViolation
from skyhaul_api.auth import routes as auth_routes
from skyhaul_api.auth.permissions import ANY_USER, PUBLIC, Permission, access_of, has_permission
from skyhaul_api.auth.tokens import AUDIENCE, ISSUER
from skyhaul_api.openapi import API_PREFIX
from skyhaul_fleet_core.models import AuditEntry, Drone, Role, User
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

LOGIN = "/api/v1/auth/login"
CONVERTER = re.compile(r"<(?:[^<>:]+:)?([^<>]+)>")

MakeUser = Callable[[Role], User]
TokenFor = Callable[[User], str]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _audit(session: Session, **where: Any) -> list[AuditEntry]:
    session.expire_all()
    statement = select(AuditEntry).filter_by(**where).order_by(AuditEntry.id)
    return list(session.scalars(statement))


def _drone_body(drone_id: str) -> dict[str, Any]:
    return {"id": drone_id, "name": "Test drone", "home": {"lat": 52.1, "lon": 4.3}}


def test_login_returns_a_15_minute_token_with_all_claims_and_me_returns_the_caller(
    password: str, api: FlaskClient, api_app: Flask, make_user: MakeUser, db_session: Session
) -> None:
    user = make_user(Role.DISPATCHER)
    response = api.post(LOGIN, json={"email": user.email, "password": password})
    body = response.get_json()
    assert response.status_code == 200
    assert set(body) == {"access_token", "token_type", "expires_in", "user"}
    assert (body["token_type"], body["expires_in"], body["user"]["id"]) == (
        "Bearer",
        900,
        str(user.id),
    )
    secret = api_app.extensions["skyhaul.config"].jwt_secret
    claims = jwt.decode(body["access_token"], secret, algorithms=["HS256"], audience=AUDIENCE)
    assert set(claims) == {"iss", "aud", "sub", "role", "iat", "exp", "jti"}
    assert (claims["iss"], claims["sub"], claims["exp"] - claims["iat"]) == (
        ISSUER,
        str(user.id),
        900,
    )
    stored = db_session.scalar(select(User.password_hash).where(User.id == user.id))
    assert stored is not None
    assert stored.startswith("$argon2id$")
    assert password not in stored

    me = api.get("/api/v1/auth/me", headers=bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.get_json() == body["user"]


def test_wrong_password_and_unknown_email_get_the_same_401_and_bad_input_is_422_unhashed(
    api: FlaskClient, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_user(Role.VIEWER)
    dummy_calls: list[str] = []
    real_dummy = auth_routes.verify_dummy
    monkeypatch.setattr(
        auth_routes, "verify_dummy", lambda p: dummy_calls.append(p) or real_dummy(p)
    )

    wrong = api.post(LOGIN, json={"email": user.email, "password": "not-the-password"})
    unknown = api.post(LOGIN, json={"email": "nobody@example.com", "password": "whatever-x"})
    assert wrong.status_code == unknown.status_code == 401
    strip = lambda body: {k: v for k, v in body.items() if k != "request_id"}  # noqa: E731
    assert strip(wrong.get_json()) == strip(unknown.get_json())
    assert dummy_calls == ["whatever-x"]

    def fail_if_hashed(*_args: Any) -> bool:
        raise AssertionError("hashed before validation")

    monkeypatch.setattr(auth_routes, "verify_password", fail_if_hashed)
    monkeypatch.setattr(auth_routes, "verify_dummy", fail_if_hashed)
    for password in ("", "x" * 1025):
        response = api.post(LOGIN, json={"email": user.email, "password": password})
        assert response.status_code == 422
        assert list(response.get_json()["fields"]) == ["password"]


def test_login_rate_limits_answer_429_with_retry_after_and_every_attempt_is_audited(
    password: str, api: FlaskClient, make_user: MakeUser, db_session: Session
) -> None:
    user = make_user(Role.VIEWER)
    first_audit_id = db_session.scalar(select(func.coalesce(func.max(AuditEntry.id), 0)))
    env = {"REMOTE_ADDR": "198.51.100.7"}
    for _ in range(5):
        api.post(LOGIN, json={"email": user.email, "password": "wrong-pass"}, environ_base=env)
    limited = api.post(LOGIN, json={"email": user.email, "password": password}, environ_base=env)
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0
    assert limited.get_json()["error"] == "rate_limited"

    # Per ip: 20 a minute across emails (6 used above, counting the refused one).
    env = {"REMOTE_ADDR": "198.51.100.8"}
    statuses = [
        api.post(
            LOGIN, json={"email": f"n{i}@example.com", "password": "pw-x"}, environ_base=env
        ).status_code
        for i in range(21)
    ]
    assert statuses[:20] == [401] * 20
    assert statuses[20] == 429

    entries = [e for e in _audit(db_session, action="auth.login") if e.id > first_audit_id]
    outcomes = [e.outcome for e in entries]
    assert outcomes.count("failure") == 25
    assert outcomes.count("denied") == 2
    text = repr([(e.data, e.target_id) for e in entries])
    assert password not in text
    assert "wrong-pass" not in text
    assert "pw-x" not in text
    assert all(e.data.get("request_id") for e in entries)


def test_bad_tokens_get_401_and_disabling_or_demoting_applies_to_the_next_request(
    api: FlaskClient, api_app: Flask, make_user: MakeUser, token_for: TokenFor, db_session: Session
) -> None:
    secret = api_app.extensions["skyhaul.config"].jwt_secret
    admin = make_user(Role.ADMIN)
    token = token_for(admin)
    claims = jwt.decode(token, secret, algorithms=["HS256"], audience=AUDIENCE)
    forged = {
        "expired": jwt.encode({**claims, "exp": claims["iat"] - 1}, secret),
        "tampered": token[:-2] + ("AA" if not token.endswith("AA") else "BB"),
        "alg none": jwt.encode(claims, None, algorithm="none"),
        "wrong iss": jwt.encode({**claims, "iss": "evil"}, secret),
        "wrong aud": jwt.encode({**claims, "aud": "evil"}, secret),
    }
    for name, bad in forged.items():
        assert api.get("/api/v1/users", headers=bearer(bad)).status_code == 401, name
    assert api.get("/api/v1/users").status_code == 401

    assert api.get("/api/v1/users", headers=bearer(token)).status_code == 200
    db_session.get_one(User, admin.id).role = Role.VIEWER
    db_session.commit()
    assert api.get("/api/v1/users", headers=bearer(token)).status_code == 403
    assert api.get("/api/v1/drones", headers=bearer(token)).status_code == 200
    db_session.get_one(User, admin.id).is_active = False
    db_session.commit()
    assert api.get("/api/v1/drones", headers=bearer(token)).status_code == 401


def _protected_routes(app: Flask) -> list[tuple[str, str, str]]:
    routes = []
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith(f"{API_PREFIX}/"):
            continue
        access = access_of(app.view_functions[rule.endpoint])
        if access in (PUBLIC, None):
            continue
        url = CONVERTER.sub(
            # uuid path parameters (user, command, mission, alert ids) need a UUID to route.
            lambda m: "missing-drone" if m.group(1) == "drone_id" else str(uuid.uuid4()),
            rule.rule,
        )
        for method in sorted((rule.methods or set()) - {"HEAD", "OPTIONS"}):
            routes.append((method, url, str(access)))
    return routes


def test_the_permission_matrix_holds_for_every_gated_route_and_denials_are_audited(
    api: FlaskClient, api_app: Flask, make_user: MakeUser, token_for: TokenFor, db_session: Session
) -> None:
    routes = _protected_routes(api_app)
    assert len(routes) >= 8
    for role in Role:
        user = make_user(role)
        token = token_for(user)
        for method, url, access in routes:
            json_body = {} if method in ("POST", "PATCH") else None
            response = api.open(url, method=method, headers=bearer(token), json=json_body)
            allowed = access == ANY_USER or has_permission(role, Permission(access))
            if allowed:
                assert response.status_code not in (401, 403), (role, method, url)
            else:
                assert response.status_code == 403, (role, method, url)
                assert response.get_json()["error"] == "forbidden"
        denied = _audit(db_session, actor_user_id=user.id, action="permission.denied")
        expected_denials = sum(
            1 for _, _, a in routes if a != ANY_USER and not has_permission(role, Permission(a))
        )
        assert len(denied) == expected_denials
        for entry in denied:
            assert entry.outcome == "denied"
            assert entry.data["route"]
            assert entry.data["request_id"]


def test_unknown_ids_are_404_for_authorized_users_and_viewers_get_403_on_writes(
    api: FlaskClient, make_user: MakeUser, token_for: TokenFor, error_validator: Any
) -> None:
    admin = bearer(token_for(make_user(Role.ADMIN)))
    viewer = bearer(token_for(make_user(Role.VIEWER)))
    missing_user = f"/api/v1/users/{uuid.uuid4()}"
    for response in (
        api.get("/api/v1/drones/no-such-drone", headers=admin),
        api.get("/api/v1/drones/no-such-drone", headers=viewer),
        api.delete("/api/v1/drones/no-such-drone", headers=admin),
        api.patch(missing_user, headers=admin, json={"role": "viewer"}),
    ):
        assert response.status_code == 404
        assert not list(error_validator.iter_errors(response.get_json()))
    for response in (
        api.delete("/api/v1/drones/no-such-drone", headers=viewer),
        api.post("/api/v1/drones", headers=viewer, json=_drone_body("v-any")),
        api.patch(missing_user, headers=viewer, json={"role": "admin"}),
        api.post("/api/v1/users", headers=viewer, json={}),
    ):
        assert response.status_code == 403


def test_user_admin_actions_are_audited_and_audit_is_admin_only(
    api: FlaskClient, make_user: MakeUser, token_for: TokenFor, db_session: Session
) -> None:
    admin_user = make_user(Role.ADMIN)
    admin = bearer(token_for(admin_user))
    email = f"new-{uuid.uuid4().hex[:8]}@example.com"
    new_password = secrets.token_urlsafe(16)
    rid_create, rid_patch = str(uuid.uuid4()), str(uuid.uuid4())
    created = api.post(
        "/api/v1/users",
        headers={**admin, "X-Request-ID": rid_create},
        json={"email": email, "password": new_password, "role": "viewer"},
    )
    assert created.status_code == 201
    new_id = created.get_json()["id"]
    assert (
        api.post(
            "/api/v1/users",
            headers=admin,
            json={"email": email, "password": new_password, "role": "viewer"},
        ).status_code
        == 409
    )
    patched = api.patch(
        f"/api/v1/users/{new_id}",
        headers={**admin, "X-Request-ID": rid_patch},
        json={"role": "dispatcher", "is_active": False},
    )
    assert patched.status_code == 200
    assert (patched.get_json()["role"], patched.get_json()["is_active"]) == ("dispatcher", False)

    entries = {e.action: e for e in _audit(db_session, target_id=new_id)}
    assert entries["user.create"].actor_user_id == admin_user.id
    assert entries["user.create"].data["request_id"] == rid_create
    assert entries["user.update"].actor_user_id == admin_user.id
    assert entries["user.update"].data["request_id"] == rid_patch
    assert new_password not in repr(entries["user.create"].data)

    audit = api.get("/api/v1/audit?limit=5", headers=admin)
    assert audit.status_code == 200
    assert len(audit.get_json()["items"]) == 5
    assert audit.get_json()["items"][0]["action"] == "user.update"
    for role in (Role.DISPATCHER, Role.VIEWER):
        token = bearer(token_for(make_user(role)))
        assert api.get("/api/v1/audit", headers=token).status_code == 403


def test_bootstrap_creates_the_first_admin_once_and_there_is_no_signup(
    api_app: Flask, api: FlaskClient, db_session: Session
) -> None:
    email = f"boot-{uuid.uuid4().hex[:8]}@example.com"
    boot_password = secrets.token_urlsafe(16)
    runner = api_app.test_cli_runner()
    result = runner.invoke(
        args=["skyhaul", "bootstrap", "--admin-email", email], input=boot_password + "\n"
    )
    assert result.exit_code == 0, result.output
    assert boot_password not in result.output
    login = api.post(LOGIN, json={"email": email, "password": boot_password})
    assert login.status_code == 200
    assert login.get_json()["user"]["role"] == "admin"

    again = runner.invoke(
        args=["skyhaul", "bootstrap", "--admin-email", email],
        input=secrets.token_urlsafe(16) + "\n",
    )
    assert again.exit_code != 0
    count = db_session.scalar(select(func.count()).select_from(User).where(User.email == email))
    assert count == 1
    signup_like = {
        r.rule
        for r in api_app.url_map.iter_rules()
        if access_of(api_app.view_functions[r.endpoint]) == PUBLIC
    }
    assert not any("signup" in r or "register" in r for r in signup_like)
    assert ("POST", "/api/v1/users") not in {
        (m, r.rule)
        for r in api_app.url_map.iter_rules()
        for m in r.methods or ()
        if access_of(api_app.view_functions[r.endpoint]) == PUBLIC
    }


def test_ensure_admin_creates_syncs_the_password_and_skips_when_unset(
    api_app: Flask, api: FlaskClient
) -> None:
    email = f"dev-{uuid.uuid4().hex[:8]}@example.com"
    first, second = "1234", secrets.token_urlsafe(16)  # a short dev password is allowed here
    runner = api_app.test_cli_runner()

    def run(password: str) -> str:
        env = {"DEV_ADMIN_EMAIL": email, "DEV_ADMIN_PASSWORD": password}
        result = runner.invoke(args=["skyhaul", "ensure-admin"], env=env)
        assert result.exit_code == 0, result.output
        assert not password or password not in result.output
        return result.output

    assert "created" in run(first)
    assert api.post(LOGIN, json={"email": email, "password": first}).status_code == 200
    assert "up to date" in run(first)
    assert "updated" in run(second)  # an edited .env changes the password
    assert api.post(LOGIN, json={"email": email, "password": first}).status_code == 401
    login = api.post(LOGIN, json={"email": email, "password": second})
    assert login.get_json()["user"]["role"] == "admin"
    assert "skipped" in run("")


def test_drone_registry_registers_refuses_taken_ids_and_soft_deletes(
    api: FlaskClient, make_user: MakeUser, token_for: TokenFor, db_session: Session
) -> None:
    admin = bearer(token_for(make_user(Role.ADMIN)))
    viewer = bearer(token_for(make_user(Role.VIEWER)))
    drone_id = f"reg-{uuid.uuid4().hex[:8]}"
    created = api.post("/api/v1/drones", headers=admin, json=_drone_body(drone_id))
    assert created.status_code == 201
    assert created.get_json()["home"] == {"lat": 52.1, "lon": 4.3}
    assert api.get(f"/api/v1/drones/{drone_id}", headers=viewer).status_code == 200
    taken = api.post("/api/v1/drones", headers=admin, json=_drone_body(drone_id))
    assert (taken.status_code, taken.get_json()["error"]) == (409, "drone_id_taken")
    assert api.post("/api/v1/drones", headers=viewer, json=_drone_body("v-x")).status_code == 403
    assert api.delete(f"/api/v1/drones/{drone_id}", headers=viewer).status_code == 403

    assert api.delete(f"/api/v1/drones/{drone_id}", headers=admin).status_code == 204
    assert api.get(f"/api/v1/drones/{drone_id}", headers=viewer).status_code == 404
    listed = api.get("/api/v1/drones?limit=200", headers=viewer).get_json()["items"]
    assert drone_id not in {d["id"] for d in listed}
    reused = api.post("/api/v1/drones", headers=admin, json=_drone_body(drone_id))
    assert reused.status_code == 409
    assert [e.action for e in _audit(db_session, target_id=drone_id)] == [
        "drone.create",
        "drone.delete",
    ]


def test_no_token_password_or_authorization_header_in_logs_or_error_bodies(
    password: str,
    api: FlaskClient,
    make_user: MakeUser,
    token_for: TokenFor,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user = make_user(Role.VIEWER)
    wrong_password = secrets.token_urlsafe(16)
    caplog.set_level(logging.DEBUG)
    token = token_for(user)
    bodies = [
        api.post(LOGIN, json={"email": user.email, "password": wrong_password}).get_data(
            as_text=True
        ),
        api.get("/api/v1/audit", headers=bearer(token)).get_data(as_text=True),
        api.get("/api/v1/users", headers=bearer(token + "x")).get_data(as_text=True),
        api.post("/api/v1/drones", headers=bearer(token), json={}).get_data(as_text=True),
    ]
    api.get("/api/v1/drones", headers=bearer(token))
    logged = caplog.text + "".join(r.getMessage() + repr(r.__dict__) for r in caplog.records)
    for text in (logged, *bodies):
        assert token not in text
        assert password not in text
        assert wrong_password not in text
        assert "Bearer " not in text
        assert "Authorization" not in text


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"id": "ab\n"}, "id"),
        ({"home": {"lat": 0, "lon": 180}}, "home.lon"),
    ],
)
def test_drone_registration_rejects_a_trailing_newline_id_and_lon_180(
    api: FlaskClient, make_user: MakeUser, token_for: TokenFor, change: dict[str, Any], field: str
) -> None:
    admin = bearer(token_for(make_user(Role.ADMIN)))
    body = {**_drone_body(f"edge-{uuid.uuid4().hex[:8]}"), **change}
    response = api.post("/api/v1/drones", headers=admin, json=body)
    assert response.status_code == 422
    assert field in response.get_json()["fields"]


def test_only_a_unique_violation_on_drone_insert_is_a_409(
    api: FlaskClient, make_user: MakeUser, token_for: TokenFor, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = bearer(token_for(make_user(Role.ADMIN)))

    real_flush = Session.flush

    def failing_flush(cause: Exception) -> Callable[..., None]:
        def flush(self: Session, *args: Any, **kwargs: Any) -> None:
            if any(isinstance(obj, Drone) for obj in self.new):
                raise IntegrityError("INSERT", {}, cause)
            real_flush(self, *args, **kwargs)

        return flush

    for cause, status in ((UniqueViolation(), 409), (CheckViolation(), 500)):
        monkeypatch.setattr(Session, "flush", failing_flush(cause))
        body = _drone_body(f"iv-{uuid.uuid4().hex[:8]}")
        response = api.post("/api/v1/drones", headers=admin, json=body)
        assert response.status_code == status, type(cause).__name__


def test_the_last_active_admin_cannot_be_demoted_or_deactivated(
    api: FlaskClient, make_user: MakeUser, token_for: TokenFor, db_session: Session
) -> None:
    admin = make_user(Role.ADMIN)
    headers = bearer(token_for(admin))
    others = list(
        db_session.scalars(
            select(User).where(User.role == Role.ADMIN, User.id != admin.id, User.is_active)
        )
    )
    for other in others:
        other.is_active = False
    db_session.commit()
    try:
        url = f"/api/v1/users/{admin.id}"
        for change in ({"role": "dispatcher"}, {"is_active": False}):
            response = api.patch(url, headers=headers, json=change)
            assert (response.status_code, response.get_json()["error"]) == (409, "last_admin")
        make_user(Role.ADMIN)
        assert api.patch(url, headers=headers, json={"role": "viewer"}).status_code == 200
    finally:
        for other in others:
            other.is_active = True
        db_session.commit()
