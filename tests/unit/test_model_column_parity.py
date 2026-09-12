"""Every database column a model owns must be mapped on that model.

Twice now a migration has added a column and the model was never updated, and
both times the failure was silent in the worst way:

  0030  `prescription_fills.inventory_lot_id` — the recall link. Assignments
        went to a plain Python attribute and were dropped at commit. The sibling
        field `lot_number` persisted, so the row looked half-written and the
        lot FK was simply absent.
  0035  `inventory_lots.quantity_damaged` and its two siblings — every read
        raised AttributeError at runtime.

Neither is visible to a type checker, a migration check, or a unit test that
never touches Postgres. Only a query against the real schema finds them, which
is what this does.

Both directions are checked, because they fail differently:

  db → model   a column exists in Postgres with no mapped attribute. Writes are
               silently discarded; reads raise. This is the bug above.
  model → db   a model declares a column Postgres does not have. Every query
               naming that table fails at once — loud, but worth catching here
               rather than in whichever endpoint runs first after a deploy.

Skipped when the disposable test database is unreachable, so the suite still
runs on a machine with no Postgres.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Every module that declares a model, imported explicitly. `Base.metadata` is
# global mutable state: if this test only imported `shared.models`, the set of
# tables it sees would depend on whichever other test happened to run first and
# import something else onto the same registry. That made this file pass alone
# and fail in the suite.
import shared.models  # noqa: F401
import services.ai.knowledge_engine.schema  # noqa: F401
from shared.models.base import Base


def _test_db_url() -> str | None:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        dotenv = Path(__file__).resolve().parents[2] / ".env"
        if dotenv.exists():
            for line in dotenv.read_text().splitlines():
                m = re.match(r"^DATABASE_URL=(.+)$", line.strip())
                if m:
                    url = re.sub(r"/pharmpilot$", "/pharmpilot_test", m.group(1))
                    break
    return url or None


URL = _test_db_url()
pytestmark = [pytest.mark.asyncio,
              pytest.mark.skipif(not URL, reason="no test database configured")]

# Tables whose model is deliberately a partial view of a wider table, or whose
# extra columns are managed outside the ORM. Every entry needs a reason: an
# unexplained exemption here is how the guard quietly stops guarding.
IGNORED_DB_COLUMNS: dict[str, set[str]] = {
    # (none today — add with a comment saying why)
}

# Tables whose models are real but whose DDL does not come from Alembic, so a
# freshly-migrated database legitimately lacks them. They are still covered by
# the column-parity checks wherever they do exist.
TABLES_OUTSIDE_ALEMBIC = {
    "knowledge_chunks", "knowledge_queries", "knowledge_sources",
}


async def _db_columns(conn) -> dict[str, set[str]]:
    rows = (await conn.execute(text("""
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
    """))).all()
    out: dict[str, set[str]] = {}
    for table, column in rows:
        out.setdefault(table, set()).add(column)
    return out


def _model_columns() -> dict[str, set[str]]:
    """Mapped columns per table, keyed by the DATABASE column name.

    `column.name` rather than the attribute name on purpose: a model may map
    `event_metadata` onto the column `metadata`, and comparing attribute names
    would report a false break for it.
    """
    return {t.name: {c.name for c in t.columns} for t in Base.metadata.tables.values()}


@pytest.fixture
async def schema():
    engine = create_async_engine(URL)
    try:
        async with engine.connect() as conn:
            db = await _db_columns(conn)
    except Exception as e:  # noqa: BLE001 — an unreachable DB is a skip, not a failure
        await engine.dispose()
        pytest.skip(f"test database unreachable: {type(e).__name__}: {e}")
    models = _model_columns()
    # Only tables that have BOTH a model and a table can be compared. A model
    # whose table is missing is the second test's business; a table with no
    # model (backups, scratch, extensions) is not this guard's business at all.
    shared = sorted(set(db) & set(models))
    yield {"db": db, "models": models, "shared": shared}
    await engine.dispose()


async def test_the_guard_is_actually_comparing_something(schema):
    """A parity test that silently compares zero tables passes for ever."""
    assert len(schema["shared"]) >= 20, (
        f"only {len(schema['shared'])} model-backed tables were compared — "
        f"the model registry or the database is not what this test assumes")


async def test_every_database_column_is_mapped_on_its_model(schema):
    """The failure this file exists for.

    A column Postgres has and the model does not: writes to it are dropped
    without error and reads raise AttributeError. Caught by nothing else.
    """
    breaks: list[str] = []
    for table in schema["shared"]:
        unmapped = (schema["db"][table] - schema["models"][table]
                    - IGNORED_DB_COLUMNS.get(table, set()))
        for column in sorted(unmapped):
            breaks.append(f"{table}.{column}")

    assert not breaks, (
        "These columns exist in the database but are not mapped on their model, "
        "so assignments to them are silently discarded and reads raise:\n  "
        + "\n  ".join(breaks)
        + "\n\nAdd the field to the model in shared/models/, matching the "
          "migration that created the column. If the column is deliberately "
          "unmapped, add it to IGNORED_DB_COLUMNS with a reason.")


async def test_every_mapped_column_exists_in_the_database(schema):
    """The other direction: a model column Postgres does not have. Loud at
    runtime, but better found here than by whichever endpoint runs first."""
    breaks: list[str] = []
    for table in schema["shared"]:
        missing = schema["models"][table] - schema["db"][table]
        for column in sorted(missing):
            breaks.append(f"{table}.{column}")

    assert not breaks, (
        "These columns are declared on a model but absent from the database — "
        "a migration is missing or was not applied:\n  " + "\n  ".join(breaks))


async def test_every_alembic_managed_model_table_exists(schema):
    """A model whose table a migration should have created but did not.

    Tables built outside Alembic are excluded by name rather than by guesswork,
    so adding one is a deliberate act with a reason attached.
    """
    missing = sorted(set(schema["models"]) - set(schema["db"])
                     - TABLES_OUTSIDE_ALEMBIC)
    assert not missing, (
        "These tables are declared by a model but do not exist in the database, "
        "and are not listed as created outside Alembic:\n  " + "\n  ".join(missing))


async def test_the_two_columns_that_caused_this_guard_are_mapped(schema):
    """Regression pins for the specific defects, so the guard cannot be
    weakened past them without a test naming them going red."""
    assert "inventory_lot_id" in schema["models"]["prescription_fills"], (
        "the recall link (migration 0030) is unmapped again")
    for column in ("quantity_damaged", "quantity_returned", "quantity_in_transit"):
        assert column in schema["models"]["inventory_lots"], (
            f"the {column} holding bucket (migration 0035) is unmapped again")
