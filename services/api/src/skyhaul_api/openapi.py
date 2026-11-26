"""The API's OpenAPI document and its comparison with the committed contract.

``contracts/openapi/fleet-api.v1.json`` is the contract (contract first, #23). The app
exports its own spec (``flask spec``) and ``make openapi-check`` compares the two:

- Everything outside ``paths`` must be equal. The document frame (info, servers, tags,
  security, ``x-socketio``) is declared here. Components the app generates itself must equal
  the contract's; the others are taken from the contract, so ``$ref``\\ s resolve.
- Paths are compared for the routes the app implements. A contract path that no route
  implements yet must be listed in ``PENDING_PATHS``; the issue that implements it removes it
  from the list. An app path missing from the contract, or a pending path that is already
  implemented, is a difference.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

from flask import Flask
from flask_smorest import Api

API_PREFIX: Final = "/api/v1"
OPENAPI_VERSION: Final = "3.1.0"

# Contract paths not implemented yet, with the issue that brings them (none left in M2).
PENDING_PATHS: Final[frozenset[str]] = frozenset()


def _schema(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


SPEC_OPTIONS: Final[dict[str, Any]] = {
    "jsonSchemaDialect": "https://json-schema.org/draft/2020-12/schema",
    "info": {
        "description": (
            "REST contract of the fleet API (services/api), and the Socket.IO event payloads "
            "it pushes. Design: docs/architecture/m2-design.md. The backend implements to "
            "this file and the UI generates types from it. Every operation needs a bearer JWT "
            "unless it has security: []. x-permission names the permission the server checks. "
            "Skyhaul serves a single organization. Lists use keyset pagination: ?limit= and an "
            "opaque ?cursor=, answered as {items, next_cursor}. Sessions: the access token is "
            "renewed by POST /auth/refresh with an httpOnly refresh cookie and an X-CSRF-Token "
            "header (ADR 0018)."
        ),
        "license": {"name": "MIT", "identifier": "MIT"},
    },
    "servers": [{"url": API_PREFIX}],
    "security": [{"bearerAuth": []}],
    "tags": [
        {"name": name}
        for name in ("auth", "users", "drones", "commands", "missions", "alerts", "audit", "health")
    ],
    "x-socketio": {
        "namespace": "/fleet",
        "auth": _schema("SocketAuth"),
        "connect_error": _schema("SocketConnectError"),
        "server_events": {
            "drone.state": _schema("DroneStateEvent"),
            "command.updated": _schema("CommandUpdatedEvent"),
            "mission.updated": _schema("MissionUpdatedEvent"),
            "alert.raised": _schema("AlertRaisedEvent"),
            "alert.updated": _schema("AlertUpdatedEvent"),
        },
        "client_events": {},
    },
}

# Building blocks for route docs.
# apispec turns a parameter given by name into a $ref to components.parameters.
REQUEST_ID_PARAMETER: Final = "RequestIdHeader"
REQUEST_ID_HEADERS: Final = {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}}


def schema_ref(name: str) -> dict[str, str]:
    return _schema(name)


def json_body(name: str) -> dict[str, Any]:
    """A required JSON request body of the contract schema ``name``."""
    return {"required": True, "content": {"application/json": {"schema": _schema(name)}}}


# Status -> the contract's shared response component.
ERROR_RESPONSES: Final = {
    400: "BadRequest",
    401: "Unauthorized",
    403: "Forbidden",
    404: "NotFound",
    409: "Conflict",
    413: "PayloadTooLarge",
    415: "UnsupportedMediaType",
    422: "Unprocessable",
    429: "TooManyRequests",
    503: "ServiceUnavailable",
}

# What every protected operation documents; operations with a body add BODY_ERRORS.
PROTECTED_ERRORS: Final = (400, 401, 403, 429)
BODY_ERRORS: Final = (413, 415, 422)


def error_doc(*statuses: int) -> dict[str, Any]:
    """``responses`` for ``Blueprint.doc``: a $ref to the shared component of each status."""
    return {
        str(status): {"$ref": f"#/components/responses/{ERROR_RESPONSES[status]}"}
        for status in statuses
    }


class FleetApi(Api):  # type: ignore[misc]  # flask-smorest ships no type information
    # Errors are documented per operation, as in the contract; no generic "default" response.
    DEFAULT_ERROR_RESPONSE_NAME = None

    # flask-smorest would add its own Error and PaginationMetadata schemas (a different
    # error shape and offset pagination). The contract's components replace them.
    def _register_responses(self) -> None:
        pass

    def _register_pagination_header(self) -> None:
        pass

    def _register_etag_headers(self) -> None:
        pass


def make_api(app: Flask) -> FleetApi:
    app.config.update(
        API_TITLE="Skyhaul fleet API",
        API_VERSION="1.0.0-m2",
        OPENAPI_VERSION=OPENAPI_VERSION,
        API_SPEC_OPTIONS=SPEC_OPTIONS,
    )
    api = FleetApi(app)
    app.extensions["skyhaul.api"] = api
    return api


def export_spec(app: Flask, contract: Mapping[str, Any]) -> dict[str, Any]:
    """The app's spec: its paths relative to the server URL, plus the contract's components
    for every component the app does not generate itself."""
    api: FleetApi = app.extensions["skyhaul.api"]
    spec: dict[str, Any] = api.spec.to_dict()
    spec["paths"] = {
        path.removeprefix(API_PREFIX): item
        for path, item in spec["paths"].items()
        if path.startswith(API_PREFIX)
    }
    path_parameters = _path_parameter_refs(contract)
    for item in spec["paths"].values():
        if "parameters" in item:
            item["parameters"] = [
                path_parameters.get((p.get("in"), p.get("name")), p) for p in item["parameters"]
            ]
    own: dict[str, dict[str, Any]] = spec.get("components", {})
    components: dict[str, dict[str, Any]] = {}
    for kind, entries in contract.get("components", {}).items():
        components[kind] = {**entries, **own.get(kind, {})}
    for kind, entries in own.items():
        components.setdefault(kind, {}).update(entries)
    spec["components"] = components
    return spec


def _path_parameter_refs(contract: Mapping[str, Any]) -> dict[tuple[Any, Any], Any]:
    """flask-smorest writes path parameters inline from the URL rule. The contract keeps them
    as components, so a path parameter becomes a $ref to the component of the same name."""
    refs: dict[tuple[Any, Any], Any] = {}
    for name, parameter in contract.get("components", {}).get("parameters", {}).items():
        if parameter.get("in") == "path":
            refs[("path", parameter["name"])] = {"$ref": f"#/components/parameters/{name}"}
    return refs


def spec_differences(
    exported: Mapping[str, Any], contract: Mapping[str, Any], app: Flask | None = None
) -> list[str]:
    """Human-readable differences between the exported spec and the contract (empty = same)."""
    problems: list[str] = []
    for key in sorted((set(exported) | set(contract)) - {"paths"}):
        problems += _differences(exported.get(key), contract.get(key), key)

    ours: Mapping[str, Any] = exported.get("paths", {})
    theirs: Mapping[str, Any] = contract.get("paths", {})
    for path in sorted(set(ours) - set(theirs)):
        problems.append(f"paths.{path}: implemented but not in the contract")
    for path in sorted(set(ours) & PENDING_PATHS):
        problems.append(f"paths.{path}: implemented, remove it from PENDING_PATHS")
    for path in sorted(set(theirs) - set(ours) - PENDING_PATHS):
        problems.append(f"paths.{path}: in the contract but not implemented")
    for path in sorted(PENDING_PATHS - set(theirs)):
        problems.append(f"paths.{path}: listed in PENDING_PATHS but not in the contract")
    for path in sorted(set(ours) & set(theirs)):
        problems += _differences(ours[path], theirs[path], f"paths.{path}")
    if app is not None:
        problems += _undocumented_routes(app, ours)
    return problems


def _differences(ours: Any, theirs: Any, where: str) -> list[str]:
    if isinstance(ours, dict) and isinstance(theirs, dict):
        found: list[str] = []
        for key in sorted(set(ours) | set(theirs), key=str):
            if key not in theirs:
                found.append(f"{where}.{key}: only in the app")
            elif key not in ours:
                found.append(f"{where}.{key}: only in the contract")
            else:
                found += _differences(ours[key], theirs[key], f"{where}.{key}")
        return found
    return [] if ours == theirs else [f"{where}: differs"]


_CONVERTER = re.compile(r"<(?:[^<>:]+:)?([^<>]+)>")


def _undocumented_routes(app: Flask, documented: Mapping[str, Any]) -> list[str]:
    """Every route under the API prefix must appear in the spec, with all its methods."""
    found: list[str] = []
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith(f"{API_PREFIX}/"):
            continue
        path = _CONVERTER.sub(r"{\1}", rule.rule.removeprefix(API_PREFIX))
        for method in sorted((rule.methods or set()) - {"HEAD", "OPTIONS"}):
            if method.lower() not in documented.get(path, {}):
                found.append(f"route {method} {API_PREFIX}{path} is not in the spec")
    return found
