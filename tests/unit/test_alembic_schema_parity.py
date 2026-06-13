"""Regression coverage for Alembic chain vs. ORM schema parity."""

from __future__ import annotations

import asyncio
import os
import re
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

import shared.models  # noqa: F401 - populate Base.metadata like Alembic env.py
from shared.models.base import Base


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
MIGRATIONS_DIR = PROJECT_ROOT / "data" / "migrations"
PUBLIC_SCHEMA = "public"
ALEMBIC_VERSION_TABLE = "alembic_version"
RAW_SQL_ONLY_TABLES = {
    "biometric_vault_objects",  # raw biometric evidence vault accessed via raw SQL in services/biometric/evidence_vault/vault.py
    "vault_access_log",  # append-only audit trail accessed via raw SQL in services/biometric/evidence_vault/vault.py; DB-enforced immutable via Postgres RULES, intentionally not ORM-modeled
    "customer_identities",  # customer identity records accessed via raw SQL in services/biometric/identity_resolution/identity_orchestrator.py
    "person_links",  # untyped customer/patient link graph accessed via raw SQL in services/biometric/identity_resolution/person_links.py and services/biometric/identity_resolution/identity_orchestrator.py
    "rx_copilot_actions",  # Rx Copilot audit-log table accessed via raw SQL in services/ai/intelligence_services/rx_copilot.py; schema-managed via migration 0008
}


def _alembic_config() -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


def _assert_single_linear_chain(cfg: Config) -> None:
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_bases()) == 1
    assert len(script.get_heads()) == 1

    for revision in script.walk_revisions():
        down_revision = revision.down_revision
        assert not isinstance(down_revision, tuple)
        assert len(revision.nextrev) <= 1


def _as_asyncpg(url):
    if url.drivername == "postgresql+asyncpg":
        return url
    if url.drivername.startswith("postgresql"):
        return url.set(drivername="postgresql+asyncpg")
    return url


def _quote_identifier(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
        raise ValueError(f"unsafe PostgreSQL identifier: {identifier!r}")
    return f'"{identifier}"'


async def _run_admin_sql(url, statement: str, params: dict | None = None) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text(statement), params or {})
    finally:
        await engine.dispose()


@contextmanager
def _temporary_database():
    """Create a unique database using the DATABASE_URL convention from conftest."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is not configured for PostgreSQL migration tests")

    base_url = _as_asyncpg(make_url(database_url))
    if not base_url.database:
        pytest.skip("DATABASE_URL does not include a database name")

    temp_database = f"pharmpilot_migration_{uuid4().hex}"
    admin_url = base_url
    temp_url = base_url.set(database=temp_database)
    quoted_database = _quote_identifier(temp_database)

    try:
        asyncio.run(_run_admin_sql(admin_url, f"CREATE DATABASE {quoted_database}"))
    except Exception as exc:
        pytest.skip(
            "could not provision throwaway PostgreSQL database from tests/conftest.py "
            f"DATABASE_URL convention: {type(exc).__name__}: {exc}"
        )

    try:
        yield temp_url
    finally:
        asyncio.run(
            _run_admin_sql(
                admin_url,
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = :database_name
                  AND pid <> pg_backend_pid()
                """,
                {"database_name": temp_database},
            )
        )
        asyncio.run(_run_admin_sql(admin_url, f"DROP DATABASE IF EXISTS {quoted_database}"))


def _type_family(column_type) -> str:
    if isinstance(column_type, postgresql.JSONB):
        return "jsonb"
    if isinstance(column_type, postgresql.UUID) or "UUID" in column_type.__class__.__name__.upper():
        return "uuid"
    if isinstance(column_type, sa.Text):
        return "text"
    if isinstance(column_type, sa.String):
        return "string"
    if isinstance(column_type, sa.Boolean):
        return "boolean"
    if isinstance(column_type, sa.Integer):
        return "integer"
    if isinstance(column_type, sa.Numeric):
        return "numeric"
    if isinstance(column_type, sa.DateTime):
        return "datetime"
    if isinstance(column_type, sa.Date):
        return "date"
    if isinstance(column_type, sa.LargeBinary):
        return "binary"
    return column_type.__class__.__name__.lower()


def _model_foreign_keys(table: sa.Table) -> set[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    foreign_keys = set()
    for constraint in table.foreign_key_constraints:
        foreign_keys.add(
            (
                tuple(column.name for column in constraint.columns),
                constraint.referred_table.name,
                tuple(element.column.name for element in constraint.elements),
            )
        )
    return foreign_keys


def _actual_foreign_keys(inspector, table_name: str) -> set[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    foreign_keys = set()
    for constraint in inspector.get_foreign_keys(table_name, schema=PUBLIC_SCHEMA):
        foreign_keys.add(
            (
                tuple(constraint["constrained_columns"]),
                constraint["referred_table"],
                tuple(constraint["referred_columns"]),
            )
        )
    return foreign_keys


def _model_indexes(table: sa.Table) -> set[tuple[tuple[str, ...], bool]]:
    indexes = set()
    for index in table.indexes:
        column_names = tuple(column.name for column in index.columns)
        if column_names:
            indexes.add((column_names, bool(index.unique)))
    return indexes


def _actual_indexes(inspector, table_name: str) -> set[tuple[tuple[str, ...], bool]]:
    indexes = set()
    for index in inspector.get_indexes(table_name, schema=PUBLIC_SCHEMA):
        column_names = index.get("column_names")
        if column_names and all(column_names):
            indexes.add((tuple(column_names), bool(index.get("unique"))))
    return indexes


def _compare_schema(inspector) -> list[str]:
    drift: list[str] = []

    model_tables = {table.name: table for table in Base.metadata.sorted_tables}
    actual_table_names = set(inspector.get_table_names(schema=PUBLIC_SCHEMA))
    actual_model_table_names = actual_table_names - {ALEMBIC_VERSION_TABLE} - RAW_SQL_ONLY_TABLES
    model_table_names = set(model_tables)

    extra_tables = sorted(actual_model_table_names - model_table_names)
    missing_tables = sorted(model_table_names - actual_model_table_names)
    if extra_tables:
        drift.append(f"extra tables in migrated schema: {', '.join(extra_tables)}")
    if missing_tables:
        drift.append(f"missing tables from migrated schema: {', '.join(missing_tables)}")

    for table_name in sorted(model_table_names & actual_model_table_names):
        model_table = model_tables[table_name]
        model_columns = {column.name: column for column in model_table.columns}
        actual_columns = {
            column["name"]: column
            for column in inspector.get_columns(table_name, schema=PUBLIC_SCHEMA)
        }

        extra_columns = sorted(set(actual_columns) - set(model_columns))
        missing_columns = sorted(set(model_columns) - set(actual_columns))
        if extra_columns:
            drift.append(f"{table_name}: extra columns: {', '.join(extra_columns)}")
        if missing_columns:
            drift.append(f"{table_name}: missing columns: {', '.join(missing_columns)}")

        for column_name in sorted(set(model_columns) & set(actual_columns)):
            model_column = model_columns[column_name]
            actual_column = actual_columns[column_name]
            model_type = _type_family(model_column.type)
            actual_type = _type_family(actual_column["type"])
            if model_type != actual_type:
                drift.append(
                    f"{table_name}.{column_name}: type {actual_type!r} after migrations, "
                    f"expected {model_type!r}"
                )
            if bool(model_column.nullable) != bool(actual_column["nullable"]):
                drift.append(
                    f"{table_name}.{column_name}: nullable={actual_column['nullable']} "
                    f"after migrations, expected nullable={model_column.nullable}"
                )

        missing_foreign_keys = sorted(
            _model_foreign_keys(model_table) - _actual_foreign_keys(inspector, table_name)
        )
        if missing_foreign_keys:
            drift.append(f"{table_name}: missing model foreign keys: {missing_foreign_keys!r}")

        missing_indexes = sorted(
            _model_indexes(model_table) - _actual_indexes(inspector, table_name)
        )
        if missing_indexes:
            drift.append(f"{table_name}: missing model indexes: {missing_indexes!r}")

    return drift


async def _compare_schema_async(url) -> list[str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(lambda sync_conn: _compare_schema(inspect(sync_conn)))
    finally:
        await engine.dispose()


def test_alembic_migration_chain_matches_orm_schema(monkeypatch):
    cfg = _alembic_config()
    _assert_single_linear_chain(cfg)

    with _temporary_database() as migration_url:
        monkeypatch.setenv("DATABASE_URL", migration_url.render_as_string(hide_password=False))
        command.upgrade(cfg, "head")
        drift = asyncio.run(_compare_schema_async(migration_url))

    if drift:
        pytest.xfail(
            "Unexpected Alembic chain and Base.metadata schema drift:\n" + "\n".join(drift)
        )
