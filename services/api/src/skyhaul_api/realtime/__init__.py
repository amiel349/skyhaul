"""Realtime push over Socket.IO (ADR 0014): ``init_realtime`` wires the namespace, the
Redis fan-out thread and the expiry sweep into the API app."""

from __future__ import annotations

from typing import Any, Final

from flask import Flask
from flask_socketio import SocketIO
from sqlalchemy.orm import Session, sessionmaker

from skyhaul_api.config import Config
from skyhaul_api.realtime.fanout import EventFanout
from skyhaul_api.realtime.socket import NAMESPACE, ExpirySweep, SessionRegistry, init_socketio

SOCKETIO_KEY: Final = "skyhaul.socketio"
FANOUT_KEY: Final = "skyhaul.fanout"
SWEEP_KEY: Final = "skyhaul.socket_sweep"


def init_realtime(app: Flask, config: Config, sessions: sessionmaker[Session]) -> SocketIO:
    registry = SessionRegistry()
    socketio = init_socketio(app, sessions, config.jwt_secret, registry)

    def emit(event: str, data: dict[str, Any], rooms: tuple[str, ...]) -> None:
        # python-socketio takes a list of rooms and sends once per socket; the stubs only
        # know a single room.
        socketio.emit(event, data, to=list(rooms), namespace=NAMESPACE)  # type: ignore[arg-type]

    def disconnect(sid: str) -> None:
        server: Any = socketio.server  # type: ignore[attr-defined]  # missing from the stubs
        server.disconnect(sid, namespace=NAMESPACE)

    fanout = EventFanout(config.redis_url, emit)
    sweep = ExpirySweep(registry, sessions, disconnect, config.socket_sweep_s)
    fanout.start()
    sweep.start()
    app.extensions[SOCKETIO_KEY] = socketio
    app.extensions[FANOUT_KEY] = fanout
    app.extensions[SWEEP_KEY] = sweep
    return socketio


def stop_realtime(app: Flask) -> None:
    for key in (FANOUT_KEY, SWEEP_KEY):
        worker = app.extensions.get(key)
        if worker is not None:
            worker.stop()
