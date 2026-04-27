"""
Error-sanitizing middleware — prevents internal details from leaking to clients.

**What it does**

1. Catches *unhandled* exceptions (anything not turned into an HTTPException by a
   route) and returns a generic ``500 Internal Server Error`` JSON body.
2. Intercepts ``HTTPException`` responses with status ``>= 500`` and replaces the
   ``detail`` field with a safe generic message, since 5xx details often contain
   stack traces, DB errors, or config names that should never reach the frontend.
3. Logs the **real** error internally (structlog JSON on stdout) with full context
   so operators can still debug.

**What it does NOT touch**

- 4xx responses (``400``, ``401``, ``403``, ``404``, ``409``, ``422``, etc.) pass
  through unchanged — their ``detail`` is intentionally client-facing.
- ``/health`` and ``/ready`` probes are excluded so load balancers see real status.

Usage::

    from middleware.error_sanitizer import register_error_handlers

    def create_app() -> FastAPI:
        app = FastAPI(...)
        register_error_handlers(app)
        ...
"""

from __future__ import annotations

import traceback
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agent_hub_core.observability.logging import get_logger

log = get_logger(__name__)

# Paths excluded from sanitization (probes need real status info).
_PASSTHROUGH_PATHS: set[str] = {"/health", "/ready"}

# Generic message the client sees for any 5xx.
_GENERIC_5XX = "Something went wrong on our end. Please try again soon."


def _is_passthrough(request: Request) -> bool:
    return request.url.path in _PASSTHROUGH_PATHS


def _error_ref() -> str:
    """Short reference ID the client can quote in support requests."""
    return uuid.uuid4().hex[:12]


def register_error_handlers(app: FastAPI) -> None:
    """
    Attach exception handlers that sanitize 5xx responses and unhandled errors.

    Call this once in ``create_app()`` **before** including routers.
    """

    @app.exception_handler(StarletteHTTPException)
    async def _sanitize_http_exception(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        """
        Re-raise 4xx unchanged.  For 5xx, log the real detail and return a
        generic body so internal config names, stack traces, etc. never leak.
        """
        if exc.status_code < 500 or _is_passthrough(request):
            # 4xx — pass through as-is; detail is intentionally client-facing.
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=getattr(exc, "headers", None),
            )

        ref = _error_ref()
        log.error(
            "http_5xx",
            status_code=exc.status_code,
            detail=exc.detail,
            path=request.url.path,
            method=request.method,
            error_ref=ref,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": _GENERIC_5XX,
                "ref": ref,
            },
        )

    @app.exception_handler(Exception)
    async def _sanitize_unhandled(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        """
        Last-resort handler for anything that slipped past route-level try/except.
        Log the full traceback internally; return a generic 500 to the client.
        """
        if _is_passthrough(request):
            # Let probes surface the real error (for load balancer visibility).
            return JSONResponse(
                status_code=500,
                content={"detail": str(exc)},
            )

        ref = _error_ref()
        log.error(
            "unhandled_exception",
            exc_type=type(exc).__qualname__,
            exc_message=str(exc),
            path=request.url.path,
            method=request.method,
            error_ref=ref,
            traceback=traceback.format_exc(),
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": _GENERIC_5XX,
                "ref": ref,
            },
        )
