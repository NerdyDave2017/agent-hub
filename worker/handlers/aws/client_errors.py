"""Normalize boto3 ``ClientError`` codes for idempotent destroy/pause."""

from __future__ import annotations

from botocore.exceptions import ClientError


def is_not_found_or_gone(exc: ClientError) -> bool:
    """Treat missing App Runner / ECS resources as success for delete/pause retries."""
    code = exc.response.get("Error", {}).get("Code", "") or ""
    if code in ("ResourceNotFoundException", "ServiceNotFoundException"):
        return True
    return "NotFound" in code
