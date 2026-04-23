"""
ECS client adapter — real calls added when provisioning is implemented.

**Phase 6 (AWS):** prefer *describe-before-create*, idempotent ``clientToken`` / ``startedBy``
set from ``job_id``, and *conditional* resource APIs so redelivery while ``running`` does not
double-provision. DB row transitions alone do not dedupe concurrent ECS calls.
"""

from __future__ import annotations

from typing import Any

import boto3

from agent_hub_core.config.settings import Settings


class ECSAdapter:
    def __init__(self, settings: Settings) -> None:
        kwargs: dict[str, Any] = {"region_name": settings.aws_region}
        if settings.aws_endpoint_url:
            kwargs["endpoint_url"] = settings.aws_endpoint_url
        if settings.aws_access_key_id and settings.aws_secret_access_key:
            kwargs["aws_access_key_id"] = settings.aws_access_key_id
            kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
        self._client = boto3.client("ecs", **kwargs)
