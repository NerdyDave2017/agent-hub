"""Shared boto3 client kwargs for worker AWS adapters (ECS, App Runner, etc.)."""

from __future__ import annotations

from typing import Any

from agent_hub_core.config.settings import Settings


def boto_client_kwargs(settings: Settings) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"region_name": settings.aws_region}
    if settings.aws_endpoint_url:
        kwargs["endpoint_url"] = settings.aws_endpoint_url
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        kwargs["aws_access_key_id"] = settings.aws_access_key_id
        kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
    return kwargs
