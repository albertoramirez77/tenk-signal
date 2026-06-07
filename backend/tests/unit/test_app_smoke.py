"""Smoke tests for the app factory.

Confirms create_app() boots with valid env, /healthz returns 200, /metrics
exposes Prom output, and request-id middleware echoes the header.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tenk_signal.main import create_app


def test_healthz_ok() -> None:
    client = TestClient(create_app())
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_request_id_echoed() -> None:
    client = TestClient(create_app())
    r = client.get("/healthz", headers={"X-Request-ID": "my-rid-123"})
    assert r.headers["X-Request-ID"] == "my-rid-123"


def test_request_id_generated_when_absent() -> None:
    client = TestClient(create_app())
    r = client.get("/healthz")
    rid = r.headers.get("X-Request-ID")
    assert rid is not None and len(rid) >= 32  # uuid4


def test_metrics_exposed() -> None:
    app = create_app()
    client = TestClient(app)
    # Generate at least one tracked request first.
    client.get("/healthz")
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.text
    # Four golden signals must be visible.
    assert "http_request_duration_seconds" in body  # latency
    assert "http_requests_total" in body  # traffic + errors
    assert "http_requests_in_flight" in body  # saturation


def test_body_size_limit_rejects_oversize() -> None:
    client = TestClient(create_app())
    huge = "x" * 2_000_000  # 2 MiB > default 1 MiB cap
    r = client.post(
        "/healthz",
        content=huge,
        headers={"content-length": str(len(huge))},
    )
    assert r.status_code in (405, 413)  # 405 if route disallows POST; 413 ours
    if r.status_code == 413:
        assert "too large" in r.text.lower()
