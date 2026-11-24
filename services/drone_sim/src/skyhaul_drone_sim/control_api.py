"""The control API: a test-only HTTP surface to spawn drones, read their state and reset a
namespace (design note section 7, ADR 0004 as amended, ADR 0009).

It runs on the simulator's fourth thread (`control-http`): werkzeug's single-threaded
server, so one request is handled at a time. Every change goes through a `JobsBoard`
method, so it takes the board lock; nothing here holds the lock itself.

Every route but `/healthz` needs `Authorization: Bearer <SIM_CONTROL_TOKEN>`, checked with
`hmac.compare_digest` before routing, so a wrong token gets 401 whatever the path and
reveals nothing about drones or namespaces. Errors are `{error, detail}` and never echo
headers. The request log has the method, path, status and duration, never headers.

Request bodies are validated with local Pydantic models: this API is not part of the
shared message interface, so it has no contracts. The OpenAPI spec is `openapi.yaml`.
"""

import hmac
import logging
import threading
import time
from collections.abc import Callable
from typing import Annotated, Any, Final, Literal

from flask import Flask, Response, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from werkzeug.exceptions import HTTPException, MethodNotAllowed
from werkzeug.serving import BaseWSGIServer, WSGIRequestHandler, make_server

from skyhaul_drone_sim.behaviors import spawn_drone
from skyhaul_drone_sim.board import (
    Job,
    JobsBoard,
    JobView,
    MakeDrone,
    ResetResult,
    SpawnConflict,
)
from skyhaul_drone_sim.clock import Clock
from skyhaul_drone_sim.config import SimConfig
from skyhaul_drone_sim.faults import Fault, FaultKind, fault_view
from skyhaul_drone_sim.model import Drone, GeoPoint
from skyhaul_drone_sim.namespaces import NAMESPACE_PATTERN, is_namespace

log = logging.getLogger(__name__)

THREAD_NAME: Final = "control-http"
MAX_BODY_BYTES: Final = 16 * 1024
SHUTDOWN_JOIN_S: Final = 2.0
POLL_S: Final = 0.2
"""How often the idle server checks for shutdown."""

type Health = Callable[[], bool]
type Stats = Callable[[], dict[str, Any]]
type Reset = Callable[[str], ResetResult]


class ResetNotConfirmed(Exception):
    """The board was reset, but a publish that began before it didn't finish in time, so a
    pre-reset message could still go out. `/reset` answers 503 and the caller retries."""


"""`Simulator.reset`: the board reset plus the wait for the publisher's in-flight message."""


# --- request bodies --------------------------------------------------------------------


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)


class Home(_Body):
    # Same bounds as SIM_HOME_*: the flat-earth physics stays away from poles and antimeridian.
    lat: float = Field(ge=-80.0, le=80.0)
    lon: float = Field(ge=-179.0, le=179.0)


class SpawnRequest(_Body):
    namespace: str = Field(pattern=NAMESPACE_PATTERN)
    count: int = Field(ge=1, le=100)
    home: Home | None = None
    battery_pct: float = Field(default=100.0, ge=0.0, le=100.0)


class ResetRequest(_Body):
    namespace: str = Field(pattern=NAMESPACE_PATTERN)


# One body per fault kind (design note section 5), told apart by `kind`. An optional
# `duration_s` has the LINK_LOSS range.
type DurationS = Annotated[float, Field(ge=1.0, le=600.0)]


class LinkLossRequest(_Body):
    kind: Literal[FaultKind.LINK_LOSS]
    duration_s: DurationS


class NackNextRequest(_Body):
    kind: Literal[FaultKind.NACK_NEXT]
    count: int = Field(ge=1, le=100)


class ReplyDelayRequest(_Body):
    kind: Literal[FaultKind.REPLY_DELAY]
    delay_s: float = Field(ge=0.1, le=30.0)
    duration_s: DurationS | None = None


class BatteryDrainRequest(_Body):
    kind: Literal[FaultKind.BATTERY_DRAIN]
    factor: float = Field(ge=1.0, le=100.0)
    duration_s: DurationS | None = None


class GpsDriftRequest(_Body):
    kind: Literal[FaultKind.GPS_DRIFT]
    drift_m: float = Field(ge=1.0, le=500.0)
    duration_s: DurationS | None = None


type FaultRequest = (
    LinkLossRequest | NackNextRequest | ReplyDelayRequest | BatteryDrainRequest | GpsDriftRequest
)
_FAULT_REQUEST: Final[TypeAdapter[FaultRequest]] = TypeAdapter(
    Annotated[FaultRequest, Field(discriminator="kind")]
)


def fault_from(body: FaultRequest, now: float) -> Fault:
    duration_s = getattr(body, "duration_s", None)
    return Fault(
        kind=body.kind,
        expires_at=None if duration_s is None else now + duration_s,
        count=getattr(body, "count", None),
        delay_s=getattr(body, "delay_s", None),
        factor=getattr(body, "factor", None),
        drift_m=getattr(body, "drift_m", None),
    )


class ApiError(Exception):
    def __init__(self, status: int, error: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.error = error
        self.detail = detail


# --- the app ---------------------------------------------------------------------------


def create_app(
    board: JobsBoard,
    cfg: SimConfig,
    *,
    clock: Clock,
    health: Health,
    stats: Stats,
    reset: Reset | None = None,
) -> Flask:
    """The control API over `board`. `health`, `stats` and `reset` come from the running
    simulator; `reset` defaults to `board.reset` (unit tests have no publisher)."""
    reset_namespace = board.reset if reset is None else reset
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES
    token = cfg.control_token.encode()

    @app.before_request
    def require_token() -> Response | None:
        g.started = time.monotonic()
        if request.path == "/healthz":
            return None
        scheme, _, given = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(given.encode(), token):
            response = _error(401, "unauthorized", "a valid bearer token is required")
            response.headers["WWW-Authenticate"] = "Bearer"
            return response
        return None

    @app.after_request
    def log_request(response: Response) -> Response:
        started = g.get("started", time.monotonic())
        # The container healthcheck polls /healthz every few seconds: keep it out of INFO.
        level = logging.DEBUG if request.path == "/healthz" else logging.INFO
        log.log(
            level,
            "control request",
            extra={
                "method": request.method,
                "path": request.path[:200],
                "status": response.status_code,
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            },
        )
        return response

    @app.errorhandler(ApiError)
    def on_api_error(exc: ApiError) -> Response:
        return _error(exc.status, exc.error, exc.detail)

    @app.errorhandler(HTTPException)
    def on_http_error(exc: HTTPException) -> Response:
        status = exc.code or 500
        name = (exc.name or "error").lower().replace(" ", "_")
        response = _error(status, name, exc.description or name)
        if isinstance(exc, MethodNotAllowed) and exc.valid_methods:
            response.headers["Allow"] = ", ".join(exc.valid_methods)
        return response

    @app.errorhandler(Exception)
    def on_unexpected(exc: Exception) -> Response:
        log.exception("control request failed", extra={"path": request.path[:200]})
        return _error(500, "internal_error", "the request could not be handled")

    @app.get("/healthz")
    def healthz() -> tuple[Response, int]:
        if health():
            return jsonify(status="ok"), 200
        return jsonify(status="degraded"), 503

    @app.post("/drones")
    def spawn() -> tuple[Response, int]:
        body = _parse(SpawnRequest)
        home = cfg_home(cfg) if body.home is None else GeoPoint(body.home.lat, body.home.lon)
        make = _maker(cfg, home=home, now=clock.monotonic(), battery_pct=body.battery_pct)
        try:
            ids = board.spawn(body.namespace, body.count, make)
        except SpawnConflict as exc:
            raise ApiError(409, "conflict", str(exc)) from None
        return jsonify(drones=[{"drone_id": drone_id} for drone_id in ids]), 201

    @app.get("/drones")
    def list_drones() -> Response:
        namespace = request.args.get("namespace", "")
        if not is_namespace(namespace):
            raise ApiError(400, "bad_request", "namespace must match ^[a-z0-9]{1,16}$")
        now = clock.monotonic()
        return jsonify(drones=[drone_summary(d, now) for d in board.drones(namespace)])

    @app.get("/drones/<drone_id>")
    def get_drone(drone_id: str) -> Response:
        found = board.drone_with_jobs(drone_id)
        if found is None:
            raise ApiError(404, "not_found", "no such drone")
        drone, jobs = found
        return jsonify(drone_detail(drone, jobs, clock.monotonic()))

    @app.delete("/drones/<drone_id>")
    def delete_drone(drone_id: str) -> Response:
        if not board.remove_drone(drone_id):
            raise ApiError(404, "not_found", "no such drone")
        return Response(status=204)

    @app.post("/drones/<drone_id>/faults")
    def add_fault(drone_id: str) -> tuple[Response, int]:
        body = _parse_with(_FAULT_REQUEST)
        now = clock.monotonic()
        fault = fault_from(body, now)
        if not board.add_fault(drone_id, fault):
            raise ApiError(404, "not_found", "no such drone")
        return jsonify(fault=fault_view(fault, now)), 201

    @app.delete("/drones/<drone_id>/faults")
    def clear_faults(drone_id: str) -> Response:
        if not board.clear_faults(drone_id):
            raise ApiError(404, "not_found", "no such drone")
        return Response(status=204)

    @app.post("/reset")
    def post_reset() -> Response:
        body = _parse(ResetRequest)
        try:
            result = reset_namespace(body.namespace)
        except ResetNotConfirmed:
            raise ApiError(
                503,
                "reset_unconfirmed",
                "the namespace was reset, but a message already being sent has not finished;"
                " retry the reset",
            ) from None
        return jsonify(
            namespace=result.namespace,
            drones_removed=result.drones_removed,
            jobs_removed=result.jobs_removed,
            epoch=result.epoch,
        )

    @app.get("/stats")
    def get_stats() -> Response:
        return jsonify(stats())

    return app


def cfg_home(cfg: SimConfig) -> GeoPoint:
    return GeoPoint(cfg.home_lat, cfg.home_lon)


def _maker(cfg: SimConfig, *, home: GeoPoint, now: float, battery_pct: float) -> MakeDrone:
    def make(drone_id: str) -> tuple[Drone, list[Job]]:
        return spawn_drone(
            drone_id,
            home=home,
            now=now,
            seed=cfg.seed,
            geofence_radius_m=cfg.geofence_radius_m,
            battery_pct=battery_pct,
        )

    return make


def _parse[T: _Body](model: type[T]) -> T:
    return _parse_with(TypeAdapter(model))


def _parse_with[T](adapter: TypeAdapter[T]) -> T:
    data = request.get_json(silent=True)
    if data is None:
        raise ApiError(400, "bad_request", "the body must be a JSON object")
    try:
        return adapter.validate_python(data)
    except ValidationError as exc:
        raise ApiError(400, "bad_request", _describe(exc)) from None


def _describe(exc: ValidationError) -> str:
    """Where and why, without the rejected input (it is not echoed back)."""
    parts = []
    for err in exc.errors(include_input=False, include_url=False)[:10]:
        where = ".".join(str(part) for part in err["loc"]) or "body"
        # pydantic's message for an unknown tag quotes the tag itself.
        why = "unknown fault kind" if err["type"] == "union_tag_invalid" else err["msg"]
        parts.append(f"{where}: {why}")
    return "; ".join(parts)


def _error(status: int, error: str, detail: str) -> Response:
    response = jsonify(error=error, detail=detail)
    response.status_code = status
    return response


# --- views -----------------------------------------------------------------------------


def drone_summary(d: Drone, now: float) -> dict[str, Any]:
    """What `GET /drones` shows for each drone."""
    mission = None
    if d.mission is not None:
        mission = {
            "mission_id": d.mission.mission_id,
            "state": d.mission.state.value,
            "waypoint_index": d.mission.index,
            "waypoints_total": len(d.mission.waypoints),
        }
    return {
        "drone_id": d.drone_id,
        "state": d.state.value,
        "armed": d.armed,
        "pos": {"lat": d.pos.lat, "lon": d.pos.lon, "altitude_m": d.altitude_m},
        "battery_pct": d.battery_pct,
        "mission": mission,
        "faults": [fault_view(fault, now) for fault in d.faults.values()],
    }


def drone_detail(d: Drone, jobs: list[JobView], now: float) -> dict[str, Any]:
    """What `GET /drones/{id}` shows: the summary plus target, cruise speed and jobs."""
    target = None
    if d.target is not None:
        target = {
            "lat": d.target.point.lat,
            "lon": d.target.point.lon,
            "altitude_m": d.target.altitude_m,
            "speed_mps": d.target.speed_mps,
        }
    return {
        **drone_summary(d, now),
        "target": target,
        "cruise_mps": d.cruise_mps,
        "jobs": [
            {
                "type": job.type.value,
                "period_s": job.period_s,
                "next_due_in_s": round(job.next_due - now, 3),
            }
            for job in jobs
        ],
    }


# --- the server thread -----------------------------------------------------------------


class _QuietHandler(WSGIRequestHandler):
    """werkzeug's handler without its access log line: `create_app` logs each request as
    JSON, without headers."""

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        pass


class ControlServer:
    """The control API on its own thread. Binds in the constructor (raises OSError if the
    port is taken), serves after `start()`, and stops with `shutdown()`."""

    def __init__(self, app: Flask, host: str, port: int) -> None:
        self._server: BaseWSGIServer = make_server(
            host, port, app, threaded=False, request_handler=_QuietHandler
        )
        self._thread = threading.Thread(target=self._serve, name=THREAD_NAME, daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.server_port)

    def start(self) -> None:
        self._thread.start()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def shutdown(self) -> None:
        """Stop serving (after the request in progress) and close the socket."""
        if self._thread.is_alive():
            self._server.shutdown()
            self._thread.join(SHUTDOWN_JOIN_S)
        self._server.server_close()

    def _serve(self) -> None:
        self._server.serve_forever(poll_interval=POLL_S)
