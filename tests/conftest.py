"""Shared pytest fixtures for all test suites."""
import os
import sys

import pytest

# Ensure project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set test environment
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot_test")
os.environ.setdefault("SECRET_KEY", "test_secret_key_not_for_production_" + "x" * 10)
os.environ.setdefault("VAULT_MASTER_KEY_HEX", "a" * 64)
os.environ.setdefault("VAULT_HMAC_SECRET_HEX", "b" * 64)
os.environ.setdefault("ANTHROPIC_API_KEY", "")


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _usable_event_loop():
    """Keep a live loop available for the sync tests that drive coroutines with
    `asyncio.get_event_loop().run_until_complete(...)`.

    pytest-asyncio (auto mode) gives every async test its own loop and closes it
    afterwards, so the next SYNC test to call get_event_loop() hits either a
    closed loop or "no current event loop" — a failure that depends purely on
    file ordering and has nothing to do with the code under test.
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    yield
