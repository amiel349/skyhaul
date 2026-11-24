"""``GET /healthz`` is public and reveals nothing (#24)."""

from __future__ import annotations

import pytest
from flask.testing import FlaskClient


@pytest.mark.parametrize("path", ["/healthz", "/api/v1/healthz"])
def test_healthz_answers_200_without_auth_and_without_secrets(
    client: FlaskClient, path: str
) -> None:
    response = client.get(path)  # no Authorization header

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
