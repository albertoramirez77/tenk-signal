"""Test fixtures.

Sets env vars before any tenk_signal import so Settings() never reads the
caller's real environment. A guard fails the suite hard if the real Anthropic
SDK is invoked.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Iterator

import pytest

# Must be set before tenk_signal.config is imported.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key-not-real")
os.environ.setdefault("ANTHROPIC_MODEL", "claude-sonnet-4-6")
os.environ.setdefault("PROMPT_VERSION", "test-v1")
os.environ.setdefault("EDGAR_USER_AGENT", "TenK Signal test@example.com")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/tenk_signal_test",
)
os.environ.setdefault("APP_API_KEY_ADMIN", "test-admin-key-must-be-long-enough-12345")
os.environ.setdefault("APP_API_KEY_VIEWER", "test-viewer-key-must-be-long-enough-12345")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("ENVIRONMENT", "ci")


@pytest.fixture(autouse=True)
def _no_live_anthropic() -> Iterator[None]:
    """Belt-and-suspenders: real Anthropic client must never be constructed
    in tests. P5 swaps in a fake; this is the early-warning siren."""
    real = sys.modules.get("anthropic")
    if real is not None and hasattr(real, "Anthropic"):
        original = real.Anthropic

        def _blocked(*args: object, **kwargs: object) -> None:
            raise RuntimeError(
                "Live Anthropic client constructed in a test — use the fake "
                "in services/extractor.py instead."
            )

        real.Anthropic = _blocked  # type: ignore[assignment]
        try:
            yield
        finally:
            real.Anthropic = original  # type: ignore[assignment]
    else:
        yield


@pytest.fixture
def admin_key() -> str:
    return os.environ["APP_API_KEY_ADMIN"]


@pytest.fixture
def viewer_key() -> str:
    return os.environ["APP_API_KEY_VIEWER"]


def pytest_unconfigure(config: pytest.Config) -> None:
    """Dispose the async SQLAlchemy engine after pytest finishes."""
    from tenk_signal.db import dispose_engine

    asyncio.run(dispose_engine())
