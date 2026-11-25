"""Skyhaul fleet API: Flask app factory."""

from __future__ import annotations

import sys

import redis
from flask import Flask
from skyhaul_contracts.codec import Codec
from skyhaul_fleet_core.db import make_engine, make_session_factory
from skyhaul_fleet_core.settings import SettingsError

from skyhaul_api import health
from skyhaul_api.audit import routes as audit_routes
from skyhaul_api.auth import routes as auth_routes
from skyhaul_api.auth.guard import init_guard
from skyhaul_api.cli import init_cli
from skyhaul_api.commands import routes as command_routes
from skyhaul_api.commands.publisher import CommandPublisher, broker_connect
from skyhaul_api.config import MAX_BODY_BYTES, Config, load_config
from skyhaul_api.database import SESSIONS_KEY, init_database
from skyhaul_api.drones import routes as drone_routes
from skyhaul_api.errors import init_errors
from skyhaul_api.logs import configure_logging, init_request_logging
from skyhaul_api.openapi import API_PREFIX, make_api
from skyhaul_api.ratelimit import init_rate_limits
from skyhaul_api.users import routes as user_routes

# gunicorn stops (instead of respawning the worker forever) when a worker exits with 3.
BOOT_ERROR_EXIT_CODE = 3

REDIS_TIMEOUT_S = 2


def create_app(config: Config | None = None, publisher: CommandPublisher | None = None) -> Flask:
    """The fleet API. Without ``config`` it reads the environment and exits on a bad setting.

    It does not connect at boot: with Postgres, Redis or RabbitMQ down it starts, and
    ``/readyz`` answers 503. The command publisher thread connects in the background.
    """
    if config is None:
        config = _config_or_exit()
    configure_logging()
    app = _create_routes_app()

    engine = make_engine(config.database_url)
    redis_client = redis.Redis.from_url(
        config.redis_url, socket_timeout=REDIS_TIMEOUT_S, socket_connect_timeout=REDIS_TIMEOUT_S
    )
    app.extensions["skyhaul.engine"] = engine
    app.extensions[SESSIONS_KEY] = make_session_factory(engine)
    app.extensions["skyhaul.redis"] = redis_client
    app.extensions["skyhaul.config"] = config
    init_rate_limits(app, config.redis_url)

    health.register_readiness_check(app, "db", health.database_check(engine))
    health.register_readiness_check(app, "redis", health.redis_check(redis_client))
    if publisher is None:
        publisher = CommandPublisher(
            broker_connect(
                config.rabbitmq_host,
                config.rabbitmq_port,
                config.rabbitmq_vhost,
                config.rabbitmq_user,
                config.rabbitmq_password,
            )
        )
        publisher.start()
    app.extensions[command_routes.PUBLISHER_KEY] = publisher
    app.extensions[command_routes.CODEC_KEY] = Codec()
    health.register_readiness_check(app, "publisher", lambda: publisher.connected)
    return app


def create_spec_app() -> Flask:
    """Routes and OpenAPI only: no settings, no connections. For ``flask spec``."""
    return _create_routes_app()


def _create_routes_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES
    init_request_logging(app)
    api = make_api(app)
    for blueprint in (
        health.blp,
        auth_routes.blp,
        user_routes.blp,
        drone_routes.blp,
        command_routes.blp,
        audit_routes.blp,
    ):
        api.register_blueprint(blueprint, url_prefix=API_PREFIX)
    # Probes call the unprefixed paths too (compose healthcheck, k8s). Not in the spec.
    app.add_url_rule("/healthz", "healthz", app.view_functions["health.healthz"])
    app.add_url_rule("/readyz", "readyz", app.view_functions["health.readyz"])
    init_errors(app)
    init_guard(app)
    init_database(app)
    init_cli(app)
    return app


def _config_or_exit() -> Config:
    try:
        return load_config()
    except SettingsError as exc:
        print(f"skyhaul-api: configuration error: {exc}", file=sys.stderr)
        raise SystemExit(BOOT_ERROR_EXIT_CODE) from None
