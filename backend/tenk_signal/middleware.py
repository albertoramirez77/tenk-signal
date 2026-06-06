"""Cross-cutting middleware: request-id, in-flight gauge, body size cap.

Order matters in main.py: BodySizeLimit (rejects early) → RequestID (tags
everything downstream) → instrumentation. Sentry plugs in separately.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from prometheus_client import Gauge
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from tenk_signal.logging import get_logger, request_id_ctx

REQUEST_ID_HEADER = "X-Request-ID"

_in_flight = Gauge(
    "http_requests_in_flight",
    "In-flight HTTP requests (saturation signal).",
)

log = get_logger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Generate or propagate X-Request-ID; bind it to the log context."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        rid = incoming if incoming and len(incoming) <= 64 else str(uuid.uuid4())
        token = request_id_ctx.set(rid)
        _in_flight.inc()
        try:
            response = await call_next(request)
        finally:
            _in_flight.dec()
            request_id_ctx.reset(token)
        response.headers[REQUEST_ID_HEADER] = rid
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized requests before they hit handlers."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > self.max_bytes:
                    return JSONResponse(
                        {"detail": "request body too large"},
                        status_code=413,
                    )
            except ValueError:
                return JSONResponse(
                    {"detail": "invalid content-length"},
                    status_code=400,
                )
        return await call_next(request)
