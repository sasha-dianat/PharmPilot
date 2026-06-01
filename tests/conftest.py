"""Shared pytest fixtures for all test suites."""
import os
import sys

import pytest

# Ensure project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set test environment
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot_test")
os.environ.setdefault("SECRET_KEY", "test_secret_key_not_for_production_" + "x" * 10)
os.environ.setdefault("VAULT_MASTER_KEY_HEX", "a" * 64)
os.environ.setdefault("VAULT_HMAC_SECRET_HEX", "b" * 64)
os.environ.setdefault("ANTHROPIC_API_KEY", "")


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"
