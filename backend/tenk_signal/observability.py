"""Sentry + Prometheus wiring.

Sentry init is a no-op when SENTRY_DSN is empty (CI, local dev). Prometheus
metrics expose the Four Golden Signals via prometheus-fastapi-instrumentator
plus our own in-flight gauge from middleware.py.
"""

from __future__ import annotations

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from tenk_signal.config import Settings


def init_sentry(settings: Settings) -> None:
    if not settings.sentry_dsn:
        return
    import sentry_sdk
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        # Conservative defaults; tune in prod.
        traces_sample_rate=0.1,
        profiles_sample_rate=0.0,
        send_default_pii=False,
        integrations=[
            FastApiIntegration(),
            StarletteIntegration(),
            AsyncioIntegration(),
        ],
    )


def init_prometheus(app: FastAPI) -> None:
    """Exposes /metrics with request latency histogram, count, and status."""
    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/metrics", "/healthz"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
