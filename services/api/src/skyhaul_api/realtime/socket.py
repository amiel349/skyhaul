"""Socket.IO namespace ``/fleet`` (ADR 0014, m2-design section 9).

Flask-SocketIO runs in ``threading`` mode: no eventlet or gevent monkey-patching, so pika,
SQLAlchemy and redis-py behave as in the rest of the API. Under gunicorn's ``gthread``
worker each socket holds one of the worker's threads; WebSocket goes through
``simple-websocket``. One process in M2, so the in-memory session list is complete.

- Connect: the token comes only from the Socket.IO ``auth`` payload. A ``token`` in the query
  string is refused (URLs get logged). The user must exist and be active; the server joins
  the rooms from the user's role in the database. Refusals answer ``connect_error`` with
  ``{"message": "unauthorized"}`` or ``{"message": "token_expired"}``.
- No client event has a handler, so anything a client sends (a ``join`` included) is ignored.
- The sweep thread disconnects sessions whose token expired or whose user was disabled.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from flask import Flask, request
from flask_socketio import SocketIO, join_room
from skyhaul_fleet_core.models import User

# python-socketio turns this one (not the builtin) into connect_error {"message": ...}.
from socketio.exceptions import ConnectionRefusedError as Refused
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from skyhaul_api.auth.tokens import ExpiredTokenError, InvalidTokenError, verify_claims
from skyhaul_api.realtime.rooms import rooms_for

log = logging.getLogger(__name__)

NAMESPACE: Final = "/fleet"
ASYNC_MODE: Final = "threading"
UNAUTHORIZED: Final = "unauthorized"
TOKEN_EXPIRED: Final = "token_expired"  # noqa: S105 - a refusal reason, not a secret


@dataclass(frozen=True)
class SocketSession:
    user_id: uuid.UUID
    expires_at: datetime


class SessionRegistry:
    """Connected sockets of this process, keyed by sid. Shared by handler and sweep threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, SocketSession] = {}

    def add(self, sid: str, session: SocketSession) -> None:
        with self._lock:
            self._sessions[sid] = session

    def remove(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)

    def snapshot(self) -> dict[str, SocketSession]:
        with self._lock:
            return dict(self._sessions)


def stale_sids(
    sessions: dict[str, SocketSession], active_user_ids: set[uuid.UUID], now: datetime
) -> list[str]:
    """Sessions whose token has expired or whose user is gone or inactive."""
    return [
        sid
        for sid, session in sessions.items()
        if session.expires_at <= now or session.user_id not in active_user_ids
    ]


def init_socketio(
    app: Flask, sessions: sessionmaker[Session], jwt_secret: str, registry: SessionRegistry
) -> SocketIO:
    socketio = SocketIO(app, async_mode=ASYNC_MODE, logger=False, engineio_logger=False)

    @socketio.on("connect", namespace=NAMESPACE)
    def on_connect(auth: Any = None) -> None:
        if "token" in request.args:
            log.warning("socket refused: token in the query string")
            raise Refused(UNAUTHORIZED)
        token = auth.get("token") if isinstance(auth, dict) else None
        if not isinstance(token, str) or not token:
            raise Refused(UNAUTHORIZED)
        try:
            claims = verify_claims(token, jwt_secret)
        except ExpiredTokenError:
            raise Refused(TOKEN_EXPIRED) from None
        except InvalidTokenError:
            raise Refused(UNAUTHORIZED) from None
        with sessions() as session:
            user = session.get(User, claims.user_id)
            if user is None or not user.is_active:
                raise Refused(UNAUTHORIZED)
            role = user.role
        sid: str = request.sid  # type: ignore[attr-defined]
        for room in rooms_for(role):
            join_room(room, sid=sid, namespace=NAMESPACE)
        registry.add(sid, SocketSession(claims.user_id, claims.expires_at))

    @socketio.on("disconnect", namespace=NAMESPACE)
    def on_disconnect(*_args: Any) -> None:
        registry.remove(request.sid)  # type: ignore[attr-defined]

    return socketio


class ExpirySweep:
    """Every ``every_s`` seconds, disconnect stale sessions. A reconnect authenticates again."""

    def __init__(
        self,
        registry: SessionRegistry,
        sessions: sessionmaker[Session],
        disconnect: Callable[[str], None],
        every_s: float,
    ) -> None:
        self._registry = registry
        self._sessions = sessions
        self._disconnect = disconnect
        self._every_s = every_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="socket-sweep", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(5)

    def _run(self) -> None:
        while not self._stop.wait(self._every_s):
            try:
                self.sweep()
            except Exception:  # a database outage must not end the sweep
                log.exception("socket sweep failed")

    def sweep(self) -> int:
        current = self._registry.snapshot()
        if not current:
            return 0
        user_ids = {s.user_id for s in current.values()}
        with self._sessions() as session:
            active = set(
                session.scalars(
                    select(User.id).where(User.id.in_(user_ids), User.is_active.is_(True))
                )
            )
        stale = stale_sids(current, active, datetime.now(UTC))
        for sid in stale:
            self._registry.remove(sid)
            self._disconnect(sid)
        if stale:
            log.info("socket sweep disconnected %d sessions", len(stale))
        return len(stale)
