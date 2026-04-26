"""AWS App Runner — create, describe, pause, resume, delete for per-agent services."""

from __future__ import annotations

from typing import Any

import boto3

from agent_hub_core.config.settings import Settings

from worker.handlers.aws._boto import boto_client_kwargs


class AppRunnerAdapter:
    def __init__(self, settings: Settings) -> None:
        self._client = boto3.client("apprunner", **boto_client_kwargs(settings))

    def create_service(
        self,
        *,
        service_name: str,
        image_identifier: str,
        access_role_arn: str,
        instance_role_arn: str,
        port: str = "8080",
        cpu: str = "1024",
        memory: str = "2048",
        auto_deployments_enabled: bool = False,
        auto_scaling_configuration_arn: str | None = None,
        vpc_connector_arn: str | None = None,
        health_check_path: str = "/health",
        tags: list[dict[str, str]] | None = None,
        runtime_environment_variables: dict[str, str] | None = None,
        client_token: str | None = None,
    ) -> dict[str, Any]:
        """
        ``CreateService`` — idempotent when ``client_token`` is stable per logical create.

        See https://docs.aws.amazon.com/apprunner/latest/api/API_CreateService.html
        """
        img_cfg: dict[str, Any] = {"Port": port}
        if runtime_environment_variables:
            img_cfg["RuntimeEnvironmentVariables"] = runtime_environment_variables
        source: dict[str, Any] = {
            "AuthenticationConfiguration": {"AccessRoleArn": access_role_arn},
            "AutoDeploymentsEnabled": auto_deployments_enabled,
            "ImageRepository": {
                "ImageIdentifier": image_identifier,
                "ImageRepositoryType": "ECR",
                "ImageConfiguration": img_cfg,
            },
        }
        body: dict[str, Any] = {
            "ServiceName": service_name,
            "SourceConfiguration": source,
            "InstanceConfiguration": {
                "Cpu": cpu,
                "Memory": memory,
                "InstanceRoleArn": instance_role_arn,
            },
            "HealthCheckConfiguration": {
                "Protocol": "HTTP",
                "Path": health_check_path,
                "Interval": 10,
                "Timeout": 5,
                "HealthyThreshold": 1,
                "UnhealthyThreshold": 5,
            },
        }
        if auto_scaling_configuration_arn:
            body["AutoScalingConfigurationArn"] = auto_scaling_configuration_arn
        if vpc_connector_arn:
            body["NetworkConfiguration"] = {
                "EgressConfiguration": {
                    "EgressType": "VPC",
                    "VpcConnectorArn": vpc_connector_arn,
                }
            }
        if tags:
            body["Tags"] = tags
        if client_token:
            body["ClientToken"] = client_token[:64]
        return self._client.create_service(**body)

    def describe_service(self, service_arn: str) -> dict[str, Any]:
        return self._client.describe_service(ServiceArn=service_arn)

    def pause_service(self, service_arn: str) -> None:
        self._client.pause_service(ServiceArn=service_arn)

    def resume_service(self, service_arn: str) -> None:
        self._client.resume_service(ServiceArn=service_arn)

    def delete_service(self, service_arn: str) -> None:
        self._client.delete_service(ServiceArn=service_arn)
