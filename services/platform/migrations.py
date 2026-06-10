"""Alembic migration entrypoints used by application startup."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from services.platform.config import settings


logger = structlog.get_logger()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
MIGRATIONS_DIR = PROJECT_ROOT / "data" / "migrations"
ALEMBIC_VERSION_TABLE = "alembic_version"
LEGACY_SENTINEL_TABLE = "prescriptions"


@dataclass(frozen=True)
class DatabaseMigrationState:
    has_alembic_version: bool
    has_legacy_sentinel: bool


def _alembic_config() -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


async def _detect_database_state_async() -> DatabaseMigrationState:
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:

            def inspect_tables(sync_conn) -> DatabaseMigrationState:
                inspector = inspect(sync_conn)
                return DatabaseMigrationState(
                    has_alembic_version=inspector.has_table(ALEMBIC_VERSION_TABLE),
                    has_legacy_sentinel=inspector.has_table(LEGACY_SENTINEL_TABLE),
                )

            return await conn.run_sync(inspect_tables)
    finally:
        await engine.dispose()


def _detect_database_state() -> DatabaseMigrationState:
    return asyncio.run(_detect_database_state_async())


def run_migrations_on_boot() -> None:
    """Bring the configured database to the current Alembic head."""
    # env.py resolves the database URL from os.environ only, while Settings may
    # have loaded it from a .env file — propagate so both target the same DB.
    # (When the var is already exported, pydantic-settings used that same value.)
    os.environ.setdefault("DATABASE_URL", settings.DATABASE_URL)
    cfg = _alembic_config()
    database_state = _detect_database_state()

    if database_state.has_alembic_version:
        command.upgrade(cfg, "head")
        return

    if database_state.has_legacy_sentinel:
        logger.warning(
            "Stamping legacy create_all database at alembic head before startup migration",
            assumption=(
                "Existing schema was created by SQLAlchemy create_all and is head-equivalent; "
                "tests/unit/test_alembic_schema_parity.py guards this assumption."
            ),
        )
        command.stamp(cfg, "head")
        command.upgrade(cfg, "head")
        return

    command.upgrade(cfg, "head")
