"""``/readyz`` against a real Postgres and Redis, stopping each in turn (#24).

The containers belong to this test alone because it stops them.
"""

from __future__ import annotations

import secrets

import pytest
from skyhaul_api import create_app
from skyhaul_api.config import Config
from skyhaul_api.health import register_readiness_check
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

pytestmark = pytest.mark.integration

# Same tags as deploy/compose.yaml.
POSTGRES_IMAGE = "postgres:16-alpine"
REDIS_IMAGE = "redis:7.4-alpine"


def test_readyz_is_200_when_dependencies_answer_and_503_naming_the_one_that_does_not() -> None:
    redis_password = secrets.token_hex(16)
    postgres = PostgresContainer(POSTGRES_IMAGE, password=secrets.token_hex(16), driver="psycopg")
    redis = RedisContainer(REDIS_IMAGE, password=redis_password)
    with postgres, redis:
        redis_host = redis.get_container_host_ip()
        redis_port = redis.get_exposed_port(6379)
        app = create_app(
            Config(
                database_url=postgres.get_connection_url(),
                redis_url=f"redis://:{redis_password}@{redis_host}:{redis_port}/0",
                jwt_secret=secrets.token_hex(32),
            )
        )
        client = app.test_client()

        # Up. The publisher is not configured until #26: reported false, not a failure.
        response = client.get("/readyz")
        assert (response.status_code, response.get_json()) == (
            200,
            {"status": "ready", "checks": {"db": True, "redis": True, "publisher": False}},
        )

        # The hook #26 uses: a configured publisher that is down makes the API unready.
        register_readiness_check(app, "publisher", lambda: False)
        response = client.get("/api/v1/readyz")
        assert response.status_code == 503
        assert response.get_json()["checks"] == {"db": True, "redis": True, "publisher": False}
        register_readiness_check(app, "publisher", None)

        redis.get_wrapped_container().stop()
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.get_json() == {
            "status": "not_ready",
            "checks": {"db": True, "redis": False, "publisher": False},
        }

        postgres.get_wrapped_container().stop()
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.get_json()["checks"] == {"db": False, "redis": False, "publisher": False}
