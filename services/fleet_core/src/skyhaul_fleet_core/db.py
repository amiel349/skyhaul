"""Engine and session factory. Creating them does not connect, so a service boots without
Postgres and reports it through its readiness check."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

CONNECT_TIMEOUT_S = 3


def make_engine(database_url: str, *, pool_size: int = 10) -> Engine:
    """A pooled engine. ``pool_pre_ping`` drops connections that died while idle."""
    return create_engine(
        database_url,
        pool_size=pool_size,
        pool_pre_ping=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_S},
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
