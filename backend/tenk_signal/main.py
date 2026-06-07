"""FastAPI app factory.

create_app() reads settings (fails fast on missing required env vars),
configures logging, wires middleware in the right order, and mounts routers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tenk_signal.config import get_settings
from tenk_signal.db import dispose_engine
from tenk_signal.logging import configure_logging, get_logger
from tenk_signal.middleware import BodySizeLimitMiddleware, RequestIDMiddleware
from tenk_signal.observability import init_prometheus, init_sentry
from tenk_signal.routers import backtest, evals, extract, health, ingest, signals


def create_app() -> FastAPI:
    settings = get_settings()  # raises on missing required vars
    configure_logging(settings.log_level)
    init_sentry(settings)

    log = get_logger(__name__)
    log.info(
        "app.startup",
        environment=settings.environment,
        model=settings.anthropic_model,
        prompt_version=settings.prompt_version,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await dispose_engine()

    app = FastAPI(
        title="TenK Signal",
        version="0.1.0",
        docs_url="/docs" if settings.environment != "prod" else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # Middleware runs in reverse-add order. We want:
    #   request → BodySize → RequestID → app
    # so add RequestID first, then BodySize.
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)

    app.include_router(health.router)
    app.include_router(signals.router)
    app.include_router(backtest.router)
    app.include_router(ingest.router)
    app.include_router(extract.router)
    app.include_router(evals.router)

    init_prometheus(app)  # mounts /metrics

    return app


app = create_app()
