"""
Error-sanitizing middleware — prevents internal details from leaking to clients.

**What it does**

1. Catches *unhandled* exceptions (anything not turned into an HTTPException by a
   route) and returns a generic ``500 Internal Server Error`` JSON body.
2. Intercepts ``HTTPException`` responses with status ``>= 500`` and replaces the
   ``detail`` field with a safe generic message, since 5xx details often contain
   stack traces, DB errors, or config names that should never reach the frontend.
3. Sanitizes **422 Validation Error** responses: strips the ``input`` field (which
   echoes back raw user data including passwords) and rewrites generic Pydantic
   messages into human-friendly text suitable for frontend rendering.
4. Logs the **real** error internally (structlog JSON on stdout) with full context
   so operators can still debug.

**What it does NOT touch**

- Most 4xx responses (``400``, ``401``, ``403``, ``404``, ``409``, etc.) pass
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
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agent_hub_core.observability.logging import get_logger

log = get_logger(__name__)

# Paths excluded from sanitization (probes need real status info).
_PASSTHROUGH_PATHS: set[str] = {"/health", "/ready"}

# Generic message the client sees for any 5xx.
_GENERIC_5XX = "Something went wrong on our end. Please try again soon."

# Fields whose values must NEVER appear in validation error responses.
# These are stripped from the ``input`` echo that Pydantic/FastAPI sends by default.
_SENSITIVE_FIELDS: set[str] = {
    "password",
    "password_confirm",
    "id_token",
    "provisional_token",
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "api_key",
}

# ---------------------------------------------------------------------------
# Human-friendly validation messages
# ---------------------------------------------------------------------------
# Map (error_type, field_name) or (error_type, *) to a readable message.
# ``{field}`` is replaced with the human-readable field label.
# ``{min}`` / ``{max}`` are replaced with constraint values when available.
# ---------------------------------------------------------------------------

# Human-readable labels for field names (body → user-facing text).
_FIELD_LABELS: dict[str, str] = {
    "email": "Email",
    "password": "Password",
    "password_confirm": "Password confirmation",
    "name": "Name",
    "display_name": "Display name",
    "workspace_name": "Workspace name",
    "tenant_slug": "Workspace URL",
    "id_token": "Google credential",
    "provisional_token": "Signup token",
}


def _field_label(loc: list[Any]) -> str:
    """Derive a human-readable label from a Pydantic ``loc`` tuple."""
    # loc is like ["body", "password"] or ["body", "password_confirm"]
    # Take the last element that's a string.
    for part in reversed(loc):
        if isinstance(part, str) and part != "body":
            return _FIELD_LABELS.get(part, part.replace("_", " ").capitalize())
    return "This field"


def _humanize_error(err: dict[str, Any]) -> dict[str, Any]:
    """
    Rewrite a single Pydantic validation error dict into a clean, frontend-safe
    version: human-friendly message, no echoed ``input``, no internal ``url``.
    """
    err_type: str = err.get("type", "")
    loc: list[Any] = err.get("loc", [])
    ctx: dict[str, Any] = err.get("ctx", {})
    label = _field_label(loc)

    # Build a human-friendly message based on the error type.
    msg = _friendly_message(err_type, label, ctx, err.get("msg", ""))

    return {
        "field": loc[-1] if loc else "unknown",
        "message": msg,
    }


def _friendly_message(
    err_type: str,
    label: str,
    ctx: dict[str, Any],
    raw_msg: str,
) -> str:
    """Return a human-friendly validation message for the given error type."""

    if err_type == "missing":
        return f"{label} is required."

    if err_type == "string_too_short":
        min_len = ctx.get("min_length", 1)
        if min_len == 1:
            return f"{label} cannot be empty."
        return f"{label} must be at least {min_len} characters."

    if err_type == "string_too_long":
        max_len = ctx.get("max_length", "")
        return f"{label} must be at most {max_len} characters."

    if err_type in ("value_error.email", "value_error"):
        # Pydantic v2 uses "value_error" for EmailStr failures
        if "email" in label.lower():
            return "Please enter a valid email address."
        # model_validator errors (e.g. passwords_match) — use the raw message
        if raw_msg:
            return raw_msg.capitalize() if not raw_msg[0].isupper() else raw_msg
        return f"{label} is not valid."

    if err_type == "string_type":
        return f"{label} must be text."

    if err_type == "json_invalid":
        return "Request body is not valid JSON."

    if err_type == "model_type":
        return "Request body is required."

    # Pydantic v2 uses "value_error" for custom validators
    if "value_error" in err_type:
        if raw_msg:
            # Custom validator messages (e.g. "passwords do not match") — pass through
            return raw_msg.capitalize() if not raw_msg[0].isupper() else raw_msg
        return f"{label} is not valid."

    # Fallback — use the raw message but capitalize it
    if raw_msg:
        clean = raw_msg.capitalize() if not raw_msg[0].isupper() else raw_msg
        return f"{label}: {clean}"

    return f"{label} is not valid."


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

    # -- 422 Validation errors: sanitize input echo + improve messages ------
    @app.exception_handler(RequestValidationError)
    async def _sanitize_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """
        Replace FastAPI's default 422 response:
        - Strip ``input`` (never echo passwords, tokens, or any raw user data)
        - Replace generic Pydantic messages with human-friendly text
        """
        raw_errors = exc.errors()

        # Log the full validation detail internally (safe — server-side only).
        log.warning(
            "validation_error",
            path=request.url.path,
            method=request.method,
            error_count=len(raw_errors),
            # Log field locations only, NOT the input values
            fields=[
                ".".join(str(p) for p in e.get("loc", []))
                for e in raw_errors
            ],
        )

        sanitized = [_humanize_error(e) for e in raw_errors]

        return JSONResponse(
            status_code=422,
            content={"errors": sanitized},
        )

    # -- 5xx HTTP exceptions: generic message + error ref -------------------
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

    # -- Unhandled exceptions: generic 500 + full internal log --------------
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
