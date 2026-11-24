"""Alembic environment. Online mode only: migrations always run against a live database."""

from __future__ import annotations

from alembic import context
from skyhaul_fleet_core.models import Base
from sqlalchemy import create_engine, pool

database_url: str = context.config.attributes["database_url"]
engine = create_engine(database_url, poolclass=pool.NullPool)

with engine.connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
