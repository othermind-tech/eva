# alembic/env.py — Async SQLAlchemy migration environment for EVA

# Load eva.env / secrets FIRST — before any import below transitively pulls
# in config/settings.py, whose module-level constants (including DATABASE_URL,
# read a few lines down via core.db.session) are evaluated once at import
# time and frozen. In normal server operation this has already run (main.py
# calls it before triggering an in-process migration), but a standalone
# `alembic upgrade` / `alembic revision --autogenerate` invocation is a
# genuinely separate process that never goes through main.py — without this,
# DATABASE_URL (and any other eva.env-configured setting alembic's imports
# touch) would silently fall back to its code default in that path. Safe to
# call twice (idempotent) if this does run inside an already-initialised
# server process.
from config.env_loader import load_eva_env
load_eva_env()

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ── Alembic config object ─────────────────────────────────────────────────────
config = context.config

# Interpret the config file's logging section (if present).
# disable_existing_loggers=False preserves EVA's ExtraFormatter handlers that
# configure_logging() set up before alembic runs.  Without this flag, fileConfig
# defaults to disable_existing_loggers=True which wipes the root handler and
# silences all EVA INFO logs for the rest of the process lifetime.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# ── Application imports ───────────────────────────────────────────────────────
# Import Base so that target_metadata reflects ALL ORM-defined tables.
# Alembic uses this to autogenerate ADD/DROP TABLE diffs.
from core.db.models import Base  # noqa: E402
target_metadata = Base.metadata

# Override the sqlalchemy.url from the runtime session config so that the
# same URL resolution logic used by the app (env var → default) applies here.
# core.db.session exposes DATABASE_URL at module level.
from core.db.session import DATABASE_URL  # noqa: E402
config.set_main_option("sqlalchemy.url", DATABASE_URL)


# ── Offline mode ──────────────────────────────────────────────────────────────
def run_migrations_offline() -> None:
    """
    Run migrations without a live DB connection.

    Emits the SQL to stdout (or a file if -x output=... is supplied).
    Useful for generating SQL scripts to review or apply manually.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online mode (async) ───────────────────────────────────────────────────────
def do_run_migrations(connection) -> None:
    """Sync callback executed inside the async connection context."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Render the migration as a single transaction per revision.
        # SQLite supports this; change to False for DDL-outside-transaction DBs.
        render_as_batch=True,   # required for SQLite ALTER TABLE emulation
        # SQLite has no native Boolean, JSON, or timezone-aware DateTime —
        # it stores them all as INTEGER/TEXT and reflects them back with
        # different type names.  Disable type comparison so alembic check
        # does not flag these storage-layer equivalences as real differences.
        compare_type=False,
        # Similarly, SQLite reflects nullable differently from the model
        # declarations.  Suppress nullable drift noise.
        compare_server_default=False,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine, connect, and drive migrations synchronously."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online mode — runs the async migration driver."""
    asyncio.run(run_async_migrations())


# ── Dispatch ──────────────────────────────────────────────────────────────────
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
