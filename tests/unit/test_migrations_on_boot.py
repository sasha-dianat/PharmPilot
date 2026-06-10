"""Unit coverage for application boot-time Alembic migration wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.platform import migrations


def test_run_migrations_upgrades_when_alembic_version_exists(monkeypatch):
    calls: list[tuple[str, object, str]] = []

    monkeypatch.setattr(
        migrations,
        "_detect_database_state",
        lambda: migrations.DatabaseMigrationState(
            has_alembic_version=True,
            has_legacy_sentinel=True,
        ),
    )
    monkeypatch.setattr(
        migrations.command,
        "upgrade",
        lambda cfg, revision: calls.append(("upgrade", cfg, revision)),
    )
    monkeypatch.setattr(
        migrations.command,
        "stamp",
        lambda cfg, revision: pytest.fail("stamp should not run for versioned databases"),
    )

    migrations.run_migrations_on_boot()

    assert [(name, revision) for name, _, revision in calls] == [("upgrade", "head")]


def test_run_migrations_stamps_legacy_database_before_upgrade(monkeypatch):
    calls: list[tuple[str, object, str]] = []

    monkeypatch.setattr(
        migrations,
        "_detect_database_state",
        lambda: migrations.DatabaseMigrationState(
            has_alembic_version=False,
            has_legacy_sentinel=True,
        ),
    )
    monkeypatch.setattr(
        migrations.command,
        "stamp",
        lambda cfg, revision: calls.append(("stamp", cfg, revision)),
    )
    monkeypatch.setattr(
        migrations.command,
        "upgrade",
        lambda cfg, revision: calls.append(("upgrade", cfg, revision)),
    )

    migrations.run_migrations_on_boot()

    assert [(name, revision) for name, _, revision in calls] == [
        ("stamp", "head"),
        ("upgrade", "head"),
    ]
    assert calls[0][1] is calls[1][1]


def test_run_migrations_upgrades_fresh_database_without_stamp(monkeypatch):
    calls: list[tuple[str, object, str]] = []

    monkeypatch.setattr(
        migrations,
        "_detect_database_state",
        lambda: migrations.DatabaseMigrationState(
            has_alembic_version=False,
            has_legacy_sentinel=False,
        ),
    )
    monkeypatch.setattr(
        migrations.command,
        "upgrade",
        lambda cfg, revision: calls.append(("upgrade", cfg, revision)),
    )
    monkeypatch.setattr(
        migrations.command,
        "stamp",
        lambda cfg, revision: pytest.fail("stamp should not run for fresh databases"),
    )

    migrations.run_migrations_on_boot()

    assert [(name, revision) for name, _, revision in calls] == [("upgrade", "head")]


def test_alembic_config_uses_absolute_existing_paths():
    cfg = migrations._alembic_config()

    ini_path = Path(cfg.config_file_name or "")
    script_location = Path(cfg.get_main_option("script_location"))

    assert ini_path.is_absolute()
    assert ini_path.exists()
    assert script_location.is_absolute()
    assert script_location.exists()


def test_run_migrations_exports_database_url_for_alembic_env(monkeypatch):
    """env.py reads os.environ only; settings may come from .env — the boot
    path must propagate the resolved URL so detection and upgrade hit the
    same database."""
    import os

    settings_url = "postgresql+asyncpg://pilot:pw@db.internal:5432/pharmpilot_pilot"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(migrations.settings, "DATABASE_URL", settings_url)
    monkeypatch.setattr(
        migrations,
        "_detect_database_state",
        lambda: migrations.DatabaseMigrationState(
            has_alembic_version=True,
            has_legacy_sentinel=True,
        ),
    )
    monkeypatch.setattr(migrations.command, "upgrade", lambda cfg, revision: None)

    migrations.run_migrations_on_boot()

    assert os.environ["DATABASE_URL"] == settings_url


def test_run_migrations_does_not_clobber_exported_database_url(monkeypatch):
    import os

    exported_url = "postgresql+asyncpg://ops:pw@explicit-env:5432/pharmpilot"
    monkeypatch.setenv("DATABASE_URL", exported_url)
    monkeypatch.setattr(
        migrations.settings, "DATABASE_URL", "postgresql+asyncpg://should/not/win"
    )
    monkeypatch.setattr(
        migrations,
        "_detect_database_state",
        lambda: migrations.DatabaseMigrationState(
            has_alembic_version=True,
            has_legacy_sentinel=True,
        ),
    )
    monkeypatch.setattr(migrations.command, "upgrade", lambda cfg, revision: None)

    migrations.run_migrations_on_boot()

    assert os.environ["DATABASE_URL"] == exported_url


@pytest.mark.anyio
async def test_lifespan_skips_migrations_when_flag_disabled(monkeypatch):
    from services.platform import main

    class StubEngine:
        def __init__(self) -> None:
            self.disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    stub_engine = StubEngine()

    monkeypatch.setattr(main.settings, "RUN_MIGRATIONS_ON_BOOT", False)
    monkeypatch.setattr(main, "engine", stub_engine)
    monkeypatch.setattr(
        main,
        "run_migrations_on_boot",
        lambda: pytest.fail("migration function should not run when boot flag is disabled"),
    )

    async with main.lifespan(main.app):
        pass

    assert stub_engine.disposed is True
