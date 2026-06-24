"""Alembic environment — async engine, URL injected from pydantic-settings.

All module models import the shared ``Base``; importing them here registers their tables
on ``Base.metadata`` so autogenerate and ``target_metadata`` see the whole schema.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import every module's models here or autogenerate will miss their tables. Each import is
# for its side effect only: registering that module's tables on Base.metadata so
# target_metadata / autogenerate sees the whole schema.
import app.chat.models  # noqa: F401
import app.documents.models  # noqa: F401
import app.identity.models  # noqa: F401
import app.ingestion.models  # noqa: F401
import app.knowledge.models  # noqa: F401
from app.platform.config import settings
from app.platform.db import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
