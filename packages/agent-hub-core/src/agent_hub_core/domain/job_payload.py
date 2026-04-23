"""Shared rules for job `payload` JSON (no secrets) — used by API schemas and `jobs_service`."""

from __future__ import annotations

from typing import Any


def assert_safe_job_payload(payload: dict[str, Any] | None) -> None:
    """Raise `ValueError` if `payload` contains secret-like keys."""
    if payload is None:
        return
    lowered = {k.lower() for k in payload}
    forbidden = {"password", "secret", "token", "authorization", "refresh_token", "access_token"}
    if lowered & forbidden:
        raise ValueError("payload must not contain secret-like keys")
