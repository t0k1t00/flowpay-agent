"""Authentication and request security helpers."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from services.runtime_config import load_runtime_config


def _extract_bearer(auth_header: str) -> str:
    if not auth_header:
        return ""
    prefix = "bearer "
    if auth_header.lower().startswith(prefix):
        return auth_header[len(prefix):].strip()
    return ""


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Enforce API-key auth when enabled by environment configuration."""

    _public_paths = {
        "/",
        "/health",
        "/health/ready",
        "/ws/logs",
    }

    async def dispatch(self, request: Request, call_next):
        cfg = load_runtime_config()
        if not cfg.require_api_key:
            return await call_next(request)

        if request.url.path in self._public_paths:
            return await call_next(request)

        supplied = request.headers.get("x-api-key", "").strip()
        if not supplied:
            supplied = _extract_bearer(request.headers.get("Authorization", ""))

        if not supplied or supplied != cfg.api_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized: valid API key is required"},
            )

        return await call_next(request)
