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

from worker.handlers.aws._boto import boto_client_kwargs


class ECSAdapter:
    def __init__(self, settings: Settings) -> None:
        self._client = boto3.client("ecs", **boto_client_kwargs(settings))

    def describe_service(self, *, cluster: str, service: str) -> dict[str, Any]:
        return self._client.describe_services(cluster=cluster, services=[service])

    def update_service_desired_count(self, *, cluster: str, service: str, desired: int) -> None:
        self._client.update_service(cluster=cluster, service=service, desiredCount=desired)

    def delete_service(self, *, cluster: str, service: str, force: bool = False) -> None:
        self._client.delete_service(cluster=cluster, service=service, force=force)
