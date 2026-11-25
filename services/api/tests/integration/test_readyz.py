"""``/readyz`` against a real Postgres, Redis and RabbitMQ, stopping each in turn (#24, #26).

The containers belong to this test alone because it stops them.
"""

from __future__ import annotations

import secrets
import time

import pytest
from flask.testing import FlaskClient
from skyhaul_api import create_app
from skyhaul_api.commands.routes import PUBLISHER_KEY
from skyhaul_api.config import Config
from skyhaul_api.realtime import stop_realtime
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.rabbitmq import RabbitMqContainer
from testcontainers.community.redis import RedisContainer

pytestmark = pytest.mark.integration

# Same tags as deploy/compose.yaml.
POSTGRES_IMAGE = "postgres:16-alpine"
REDIS_IMAGE = "redis:7.4-alpine"
RABBITMQ_IMAGE = "rabbitmq:3.13-management-alpine"


def _checks_become(client: FlaskClient, expected: dict[str, bool], timeout_s: float = 20) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        body = client.get("/readyz").get_json()
        if body["checks"] == expected:
            return
        assert time.monotonic() < deadline, body
        time.sleep(0.2)


@pytest.mark.timeout(180)
def test_readyz_is_200_when_dependencies_answer_and_503_naming_the_one_that_does_not() -> None:
    redis_password, amqp_password = secrets.token_hex(16), secrets.token_hex(16)
    postgres = PostgresContainer(POSTGRES_IMAGE, password=secrets.token_hex(16), driver="psycopg")
    redis = RedisContainer(REDIS_IMAGE, password=redis_password)
    rabbit = RabbitMqContainer(RABBITMQ_IMAGE, username="skyhaul-test", password=amqp_password)
    with postgres, redis, rabbit:
        redis_host = redis.get_container_host_ip()
        redis_port = redis.get_exposed_port(6379)
        app = create_app(
            Config(
                database_url=postgres.get_connection_url(),
                redis_url=f"redis://:{redis_password}@{redis_host}:{redis_port}/0",
                jwt_secret=secrets.token_hex(32),
                rabbitmq_host=rabbit.get_container_host_ip(),
                rabbitmq_port=int(rabbit.get_exposed_port(5672)),
                rabbitmq_user="skyhaul-test",
                rabbitmq_password=amqp_password,
            )
        )
        client = app.test_client()
        try:
            # Up: the command publisher connects in the background.
            _checks_become(client, {"db": True, "redis": True, "publisher": True})
            assert client.get("/api/v1/readyz").status_code == 200

            # The broker goes away: the publisher's connection drops and the API is unready.
            rabbit.get_wrapped_container().stop()
            _checks_become(client, {"db": True, "redis": True, "publisher": False})
            response = client.get("/readyz")
            assert (response.status_code, response.get_json()["status"]) == (503, "not_ready")

            redis.get_wrapped_container().stop()
            response = client.get("/readyz")
            assert response.status_code == 503
            assert response.get_json()["checks"] == {
                "db": True,
                "redis": False,
                "publisher": False,
            }

            postgres.get_wrapped_container().stop()
            response = client.get("/readyz")
            assert response.get_json()["checks"] == {
                "db": False,
                "redis": False,
                "publisher": False,
            }
        finally:
            app.extensions[PUBLISHER_KEY].stop()
            stop_realtime(app)
