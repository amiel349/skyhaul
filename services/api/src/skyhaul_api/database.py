"""One SQLAlchemy session per request, closed when the request ends."""

from __future__ import annotations

from flask import Flask, current_app, g
from sqlalchemy.orm import Session, sessionmaker

SESSIONS_KEY = "skyhaul.sessions"


def db() -> Session:
    session: Session | None = g.get("db_session")
    if session is None:
        factory: sessionmaker[Session] = current_app.extensions[SESSIONS_KEY]
        session = factory()
        g.db_session = session
    return session


def new_session() -> Session:
    """A session apart from the request's, for writes that must survive its rollback."""
    factory: sessionmaker[Session] = current_app.extensions[SESSIONS_KEY]
    return factory()


def init_database(app: Flask) -> None:
    @app.teardown_appcontext
    def close_session(_exc: BaseException | None) -> None:
        session: Session | None = g.pop("db_session", None)
        if session is not None:
            session.close()
