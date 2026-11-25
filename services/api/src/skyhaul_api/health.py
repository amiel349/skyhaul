"""``/healthz`` (the process is alive) and ``/readyz`` (its dependencies answer). Both public.

Readiness checks are registered by name. The contract names three: ``db``, ``redis`` and
``publisher``. A name registered with ``None`` is not configured yet: it reports ``false``
and does not make the API unready. The command publisher stays that way until #26 wires it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import redis
from flask import Flask, Response, current_app, jsonify
from flask_smorest import Blueprint
from sqlalchemy import Engine, text

from skyhaul_api.auth.permissions import public
from skyhaul_api.openapi import REQUEST_ID_HEADERS, REQUEST_ID_PARAMETER, schema_ref

log = logging.getLogger(__name__)

ReadinessCheck = Callable[[], bool]
CHECKS_KEY = "skyhaul.readiness"

blp = Blueprint("health", __name__)


def register_readiness_check(app: Flask, name: str, check: ReadinessCheck | None) -> None:
    app.extensions.setdefault(CHECKS_KEY, {})[name] = check


def database_check(engine: Engine) -> ReadinessCheck:
    def check() -> bool:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:  # any driver or pool error means "not ready"
            log.warning("readiness: database unavailable (%s)", type(exc).__name__)
            return False
        return True

    return check


def redis_check(client: redis.Redis) -> ReadinessCheck:
    def check() -> bool:
        try:
            client.ping()
        except (redis.RedisError, OSError) as exc:
            log.warning("readiness: redis unavailable (%s)", type(exc).__name__)
            return False
        return True

    return check


@blp.route("/healthz", methods=["GET"])
@blp.doc(
    operationId="healthz",
    summary="Process is alive",
    security=[],
    parameters=[REQUEST_ID_PARAMETER],
)
@blp.response(200, schema_ref("Health"), description="OK", headers=REQUEST_ID_HEADERS)
@public
def healthz() -> Response:
    return jsonify(status="ok")


@blp.route("/readyz", methods=["GET"])
@blp.doc(
    operationId="readyz",
    summary="Database, Redis and command publisher are reachable",
    security=[],
    parameters=[REQUEST_ID_PARAMETER],
)
@blp.response(200, schema_ref("Readiness"), description="Ready", headers=REQUEST_ID_HEADERS)
@blp.alt_response(503, schema=schema_ref("Readiness"), description="Not ready")
@public
def readyz() -> Response:
    checks: dict[str, ReadinessCheck | None] = current_app.extensions.get(CHECKS_KEY, {})
    results = {name: check() if check else False for name, check in checks.items()}
    ready = all(results[name] for name, check in checks.items() if check is not None)
    response = jsonify(status="ready" if ready else "not_ready", checks=results)
    response.status_code = 200 if ready else 503
    return response
