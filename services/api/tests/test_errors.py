"""Every error has the contract's shape and the request id; 413 above 64 KiB (#24)."""

from __future__ import annotations

import json
import logging
import uuid

import pytest
from flask import Flask
from flask.testing import FlaskClient
from jsonschema import Draft202012Validator
from skyhaul_api.logs import JsonFormatter

SENSITIVE_HEADER = "Bearer header-value-that-must-not-leak"
SENSITIVE_INPUT = "input-value-that-must-not-leak"


def _access_line(caplog: pytest.LogCaptureFixture) -> dict[str, object]:
    records = [r for r in caplog.records if r.name == "skyhaul_api.access"]
    line: dict[str, object] = json.loads(JsonFormatter().format(records[-1]))
    return line


@pytest.mark.parametrize(
    ("method", "path", "kwargs", "status", "error"),
    [
        ("get", "/api/v1/nowhere", {}, 404, "not_found"),
        ("delete", "/api/v1/healthz", {}, 405, "method_not_allowed"),
        (
            "post",
            "/api/v1/_test/echo",
            {"data": '{"x": "' + SENSITIVE_INPUT, "content_type": "application/json"},
            400,
            "bad_request",
        ),
        (
            "post",
            "/api/v1/_test/echo",
            {"data": SENSITIVE_INPUT, "content_type": "text/plain"},
            415,
            "unsupported_media_type",
        ),
        ("get", "/api/v1/_test/fault", {}, 500, "internal_error"),
    ],
)
def test_errors_have_the_contract_shape_and_carry_the_request_id(
    client: FlaskClient,
    error_validator: Draft202012Validator,
    caplog: pytest.LogCaptureFixture,
    fault_secret: str,
    method: str,
    path: str,
    kwargs: dict[str, str],
    status: int,
    error: str,
) -> None:
    caplog.set_level(logging.INFO)
    request_id = str(uuid.uuid4())
    headers = {"X-Request-ID": request_id, "Authorization": SENSITIVE_HEADER}

    response = getattr(client, method)(path, headers=headers, **kwargs)

    body = response.get_json()
    assert response.status_code == status
    assert response.mimetype == "application/json"
    error_validator.validate(body)
    assert body["error"] == error
    assert body["request_id"] == request_id == response.headers["X-Request-ID"]
    raw = response.get_data(as_text=True)
    for leak in (SENSITIVE_HEADER, "header-value", SENSITIVE_INPUT, fault_secret, "Traceback"):
        assert leak not in raw
    line = _access_line(caplog)
    assert line["request_id"] == request_id
    assert line["status"] == status
    assert "header-value" not in json.dumps(line)


@pytest.mark.parametrize("sent", [None, "not-a-uuid", "x" * 200])
def test_a_missing_or_invalid_request_id_is_replaced_not_echoed(
    client: FlaskClient, caplog: pytest.LogCaptureFixture, sent: str | None
) -> None:
    caplog.set_level(logging.INFO)
    headers = {"X-Request-ID": sent} if sent else {}

    response = client.get("/api/v1/nowhere", headers=headers)

    generated = response.headers["X-Request-ID"]
    assert generated != sent
    assert str(uuid.UUID(generated)) == generated
    assert response.get_json()["request_id"] == generated
    assert _access_line(caplog)["request_id"] == generated


def test_a_body_over_64_kib_gets_413_with_the_error_shape(
    client: FlaskClient, error_validator: Draft202012Validator
) -> None:
    body = json.dumps({"pad": "x" * (64 * 1024)})

    response = client.post("/api/v1/_test/echo", data=body, content_type="application/json")

    assert response.status_code == 413
    error_validator.validate(response.get_json())
    assert response.get_json()["error"] == "payload_too_large"
    assert "X-Request-ID" in response.headers


def test_a_log_records_own_correlation_id_is_kept_during_a_request(app: Flask) -> None:
    import logging

    from skyhaul_api.logs import RequestIdFilter

    record = logging.LogRecord("t", logging.INFO, __file__, 1, "command sent", None, None)
    record.correlation_id = "command-correlation"
    plain = logging.LogRecord("t", logging.INFO, __file__, 1, "request log", None, None)

    with app.test_request_context(headers={"X-Request-ID": "6f1f2a62-3a5c-4c7e-9a1b-2b3c4d5e6f70"}):
        RequestIdFilter().filter(record)
        RequestIdFilter().filter(plain)

    assert record.correlation_id == "command-correlation"
    assert record.request_id == "6f1f2a62-3a5c-4c7e-9a1b-2b3c4d5e6f70"
    assert plain.correlation_id == plain.request_id == "6f1f2a62-3a5c-4c7e-9a1b-2b3c4d5e6f70"
