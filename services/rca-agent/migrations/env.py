"""Alembic environment — async, URL from ``DB_URL``.

Uses a dedicated version table (``alembic_version_rca``) so the rca-agent's
migration lineage can live in the same PostgreSQL database as the
incident-correlator's without the two ``alembic_version`` tables colliding
(ADR-019).
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from rca_agent.config import DbSettings
from rca_agent.db.models import Base

_VERSION_TABLE = "alembic_version_rca"

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", DbSettings().url)
target_metadata = Base.metadata


def _run_sync(connection: object) -> None:
    context.configure(
        connection=connection,  # type: ignore[arg-type]
        target_metadata=target_metadata,
        version_table=_VERSION_TABLE,
        compare_type=True,
        render_as_batch=True,  # so SQLite can run these too
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_run_sync)
    await connectable.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        version_table=_VERSION_TABLE,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(_run_online())
