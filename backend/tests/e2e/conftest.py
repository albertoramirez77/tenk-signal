"""E2E test setup: ensure the database schema exists before any e2e test.

The e2e suite's ``clean_db`` fixture runs ``TRUNCATE`` on the load-bearing
tables before each test. That fails if the tables were never created —
which is exactly what happened in CI, because ``backend.yml`` runs
``pytest`` directly without an Alembic step in between.

We fix this with a session-scoped autouse fixture that runs
``Base.metadata.create_all`` against the test database. It is:

* **Idempotent.** Tables that already exist are left alone, so the
  fixture is a no-op when CI has separately run ``alembic upgrade head``.
* **Skip-safe.** When Postgres isn't reachable (local pytest without a
  database), the fixture yields without touching anything. The actual
  e2e tests are already gated by the same probe at module scope (see
  ``test_pipeline_on_fixtures.py``), so nothing tries to use a schema
  that wasn't created.
* **Fast.** ``create_all`` is one round trip; running ``alembic upgrade
  head`` from a fixture would add an event-loop wrinkle without buying
  anything we don't already get from
  ``.github/workflows/migrations.yml`` (which exercises the migration
  up/down round-trip on every PR).
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from urllib.parse import urlparse

import pytest


def _postgres_reachable() -> bool:
    """Cheap TCP probe — same logic as test_pipeline_on_fixtures."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return False
    try:
        parsed = urlparse(url.replace("+asyncpg", "").replace("+psycopg", ""))
    except ValueError:
        return False
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session", autouse=True)
def _ensure_schema() -> Iterator[None]:
    if not _postgres_reachable():
        yield
        return

    # Imports inside the fixture so the module collects even if the
    # backend package somehow fails to import (gives clearer errors).
    from sqlalchemy import create_engine

    from tenk_signal import models  # noqa: F401 -- register models with Base
    from tenk_signal.config import get_settings
    from tenk_signal.db import Base

    settings = get_settings()
    engine = create_engine(settings.sync_database_url)
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    yield
