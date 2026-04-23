"""Secrets Manager client adapter (scaffold)."""

from __future__ import annotations

from typing import Any

import boto3

from agent_hub_core.config.settings import Settings


class SecretsManagerAdapter:
    def __init__(self, settings: Settings) -> None:
        kwargs: dict[str, Any] = {"region_name": settings.aws_region}
        if settings.aws_endpoint_url:
            kwargs["endpoint_url"] = settings.aws_endpoint_url
        if settings.aws_access_key_id and settings.aws_secret_access_key:
            kwargs["aws_access_key_id"] = settings.aws_access_key_id
            kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
        self._client = boto3.client("secretsmanager", **kwargs)
