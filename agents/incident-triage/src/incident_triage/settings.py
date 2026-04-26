"""Pydantic settings from env/``.env``; ``resolve_secrets()`` fills tokens/URLs from AWS ARNs when set."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import boto3
from pydantic import AliasChoices, Field, PrivateAttr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[4]
_AGENT_ENV = _REPO_ROOT / "agents" / "incident-triage" / ".env"
_ENV_FILES: tuple[str, ...] = (
    ".env",
    str(_AGENT_ENV),
) if _AGENT_ENV.is_file() else (".env",)


class Settings(BaseSettings):
    """Runtime config: HTTP bind, tenant/agent identity, secret ARNs, resolved credentials."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="incident-triage", validation_alias="APP_NAME")
    api_v1_prefix: str = Field(default="/api/v1", validation_alias="API_V1_PREFIX")
    host: str = Field(default="0.0.0.0", validation_alias="HOST")
    port: int = Field(default=8001, validation_alias="PORT")

    tenant_id: str = Field(
        default="",
        validation_alias=AliasChoices("TENANT_ID", "AGENT_HUB_TENANT_ID"),
    )
    agent_id: str = Field(
        default="",
        validation_alias=AliasChoices("AGENT_ID", "AGENT_HUB_AGENT_ID"),
    )
    hub_base_url: str = Field(default="", validation_alias="HUB_BASE_URL")
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")

    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        validation_alias="LANGFUSE_HOST",
    )
    slack_ops_channel: str = Field(default="#ops-alerts", validation_alias="SLACK_OPS_CHANNEL")

    gmail_secret_arn: str = Field(
        default="",
        validation_alias=AliasChoices("GMAIL_SECRET_ARN", "GMAIL_CREDENTIALS_ARN"),
    )
    slack_secret_arn: str = Field(
        default="",
        validation_alias=AliasChoices("SLACK_SECRET_ARN", "SLACK_TOKEN_ARN"),
    )
    hub_token_secret_arn: str = Field(
        default="",
        validation_alias=AliasChoices("HUB_TOKEN_SECRET_ARN", "HUB_SERVICE_TOKEN_ARN"),
    )
    langfuse_secret_arn: str = Field(default="", validation_alias="LANGFUSE_SECRET_ARN")
    database_secret_arn: str = Field(default="", validation_alias="DATABASE_SECRET_ARN")
    openai_secret_arn: str = Field(default="", validation_alias="OPENAI_SECRET_ARN")

    aws_region: str = Field(default="", validation_alias="AWS_REGION")

    gmail_credentials: dict[str, Any] = Field(default_factory=dict, validation_alias="GMAIL_CREDENTIALS")
    slack_bot_token: str = Field(default="", validation_alias="SLACK_BOT_TOKEN")
    hub_service_token: str = Field(default="", validation_alias="HUB_SERVICE_TOKEN")
    langfuse_public_key: str = Field(default="", validation_alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", validation_alias="LANGFUSE_SECRET_KEY")
    database_url: str = Field(default="", validation_alias="DATABASE_URL")
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    classify_model: str = Field(
        default="gpt-4o-mini",
        validation_alias="CLASSIFY_MODEL",
    )

    gmail_mark_read: bool = Field(default=True, validation_alias="GMAIL_MARK_READ")
    gmail_poll_interval_seconds: int = Field(
        default=0,
        ge=0,
        validation_alias="GMAIL_POLL_INTERVAL_SECONDS",
    )
    gmail_user_id: str = Field(default="me", validation_alias="GMAIL_USER_ID")

    _hydrated_from_secrets_manager: bool = PrivateAttr(default=False)

    @field_validator("gmail_credentials", mode="before")
    @classmethod
    def _parse_gmail_credentials(cls, value: object) -> object:
        if value is None or value == "":
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return json.loads(value)
        return value

    def has_any_secret_arn(self) -> bool:
        return any(
            arn.strip()
            for arn in (
                self.gmail_secret_arn,
                self.slack_secret_arn,
                self.hub_token_secret_arn,
                self.langfuse_secret_arn,
                self.database_secret_arn,
                self.openai_secret_arn,
            )
        )

    @property
    def secrets_manager_hydration(self) -> bool:
        return self._hydrated_from_secrets_manager

    def resolve_secrets(self) -> None:
        if not self.has_any_secret_arn():
            self._hydrated_from_secrets_manager = False
            return

        client_kwargs: dict[str, str] = {}
        if self.aws_region.strip():
            client_kwargs["region_name"] = self.aws_region.strip()

        sm = boto3.client("secretsmanager", **client_kwargs)

        def fetch_string(arn: str) -> str:
            return sm.get_secret_value(SecretId=arn)["SecretString"]

        if self.gmail_secret_arn.strip():
            self.gmail_credentials = json.loads(fetch_string(self.gmail_secret_arn))
        if self.slack_secret_arn.strip():
            raw_slack = fetch_string(self.slack_secret_arn)
            try:
                parsed = json.loads(raw_slack)
                if isinstance(parsed, dict) and parsed.get("bot_token"):
                    self.slack_bot_token = str(parsed["bot_token"])
                else:
                    self.slack_bot_token = raw_slack.strip()
            except json.JSONDecodeError:
                self.slack_bot_token = raw_slack.strip()
        if self.hub_token_secret_arn.strip():
            self.hub_service_token = fetch_string(self.hub_token_secret_arn)
        if self.langfuse_secret_arn.strip():
            payload = json.loads(fetch_string(self.langfuse_secret_arn))
            self.langfuse_public_key = payload["public_key"]
            self.langfuse_secret_key = payload["secret_key"]
        if self.database_secret_arn.strip():
            self.database_url = fetch_string(self.database_secret_arn)
        if self.openai_secret_arn.strip():
            raw_openai = fetch_string(self.openai_secret_arn)
            try:
                parsed = json.loads(raw_openai)
                if isinstance(parsed, dict) and parsed.get("api_key"):
                    self.openai_api_key = str(parsed["api_key"])
                elif isinstance(parsed, dict) and parsed.get("OPENAI_API_KEY"):
                    self.openai_api_key = str(parsed["OPENAI_API_KEY"])
                else:
                    self.openai_api_key = raw_openai.strip()
            except json.JSONDecodeError:
                self.openai_api_key = raw_openai.strip()

        self._hydrated_from_secrets_manager = True


@lru_cache
def get_settings() -> Settings:
    """Singleton settings object; clear cache in tests when env vars change."""
    return Settings()
