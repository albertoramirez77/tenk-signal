"""API-key auth with two roles: admin (writes/compute) and viewer (reads).

Both keys come from env. The check is constant-time. Wrong/missing → 401;
wrong role → 403. The router declares which role it requires via the
dependency on `require_admin` or `require_viewer`.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from tenk_signal.config import Settings, get_settings

API_KEY_HEADER = "X-API-Key"
_api_key_scheme = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


class Role(StrEnum):
    ADMIN = "admin"
    VIEWER = "viewer"


def _resolve_role(key: str | None, settings: Settings) -> Role | None:
    if not key:
        return None
    if secrets.compare_digest(key, settings.app_api_key_admin.get_secret_value()):
        return Role.ADMIN
    if secrets.compare_digest(key, settings.app_api_key_viewer.get_secret_value()):
        return Role.VIEWER
    return None


def _require(role: Role) -> Callable[..., Any]:
    def dep(
        key: str | None = Depends(_api_key_scheme),
        settings: Settings = Depends(get_settings),
    ) -> Role:
        resolved = _resolve_role(key, settings)
        if resolved is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing or invalid API key",
                headers={"WWW-Authenticate": "API-Key"},
            )
        # Admin can do anything; viewer only reads.
        if role == Role.ADMIN and resolved != Role.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="admin role required",
            )
        return resolved

    return dep


require_admin = _require(Role.ADMIN)
require_viewer = _require(Role.VIEWER)
