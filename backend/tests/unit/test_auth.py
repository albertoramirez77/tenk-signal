"""Auth role enforcement.

Two endpoints are mounted at module scope so we can hit them without spinning
up the real routers. The keys come from conftest fixtures.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from tenk_signal.auth import Role, require_admin, require_viewer


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/viewer-only")
    def viewer_route(role: Role = Depends(require_viewer)) -> dict[str, str]:
        return {"role": role}

    @app.post("/admin-only")
    def admin_route(role: Role = Depends(require_admin)) -> dict[str, str]:
        return {"role": role}

    return app


def test_missing_key_returns_401() -> None:
    client = TestClient(_app())
    r = client.get("/viewer-only")
    assert r.status_code == 401


def test_invalid_key_returns_401() -> None:
    client = TestClient(_app())
    r = client.get("/viewer-only", headers={"X-API-Key": "nope"})
    assert r.status_code == 401


def test_viewer_key_passes_viewer_route(viewer_key: str) -> None:
    client = TestClient(_app())
    r = client.get("/viewer-only", headers={"X-API-Key": viewer_key})
    assert r.status_code == 200
    assert r.json() == {"role": "viewer"}


def test_admin_key_passes_viewer_route(admin_key: str) -> None:
    """Admin is a superset of viewer."""
    client = TestClient(_app())
    r = client.get("/viewer-only", headers={"X-API-Key": admin_key})
    assert r.status_code == 200
    assert r.json() == {"role": "admin"}


def test_viewer_key_blocked_from_admin_route(viewer_key: str) -> None:
    client = TestClient(_app())
    r = client.post("/admin-only", headers={"X-API-Key": viewer_key})
    assert r.status_code == 403


def test_admin_key_passes_admin_route(admin_key: str) -> None:
    client = TestClient(_app())
    r = client.post("/admin-only", headers={"X-API-Key": admin_key})
    assert r.status_code == 200
    assert r.json() == {"role": "admin"}
