"""
Central configuration loaded from environment variables (and optional `.env`).

Alembic (`agent_hub_core.migrations.env`) imports the same `Settings` so migrations and
runtimes agree on the database URL.

`DATABASE_URL` may be omitted in local dev: discrete `DATABASE_*` fields build a URL
that matches the repo’s `docker-compose.yml` Postgres defaults.

For local development, if `./.env` is missing, `backend/.env` is loaded when present
(relative to the repository root) so `uv run ... alembic` from the repo root still
finds the same values as `uv run --directory backend uvicorn ...`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, PostgresDsn, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[5]
_ENV = _REPO_ROOT / ".env"
_ENV_FILES: tuple[str, ...] = (
    ".env",
    str(_ENV),
) if _ENV.is_file() else (".env",)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="agent-hub", validation_alias="APP_NAME")
    api_v1_prefix: str = Field(default="/api/v1", validation_alias="API_V1_PREFIX")

    database_url: PostgresDsn | None = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_URL"),
    )

    database_host: str = Field(default="localhost", validation_alias="DATABASE_HOST")
    database_port: int = Field(default=5432, validation_alias="DATABASE_PORT")
    database_user: str = Field(default="postgres", validation_alias="DATABASE_USER")
    database_password: str = Field(default="postgres", validation_alias="DATABASE_PASSWORD")
    database_name: str = Field(default="postgres", validation_alias="DATABASE_NAME")

    aws_region: str = Field(default="us-east-1", validation_alias=AliasChoices("AWS_REGION", "AWS_DEFAULT_REGION"))
    aws_endpoint_url: str | None = Field(
        default=None,
        validation_alias="AWS_ENDPOINT_URL",
        description="LocalStack base URL, e.g. http://localhost:4566. Omit in production.",
    )
    aws_access_key_id: str | None = Field(
        default=None,
        validation_alias="AWS_ACCESS_KEY_ID",
        description="Omit in ECS (IAM role). For LocalStack set to `test` with `AWS_SECRET_ACCESS_KEY`.",
    )
    aws_secret_access_key: str | None = Field(
        default=None,
        validation_alias="AWS_SECRET_ACCESS_KEY",
        description="Omit in ECS (IAM role). For LocalStack set to `test` with `AWS_ACCESS_KEY_ID`.",
    )
    sqs_queue_url: str | None = Field(
        default=None,
        validation_alias="SQS_QUEUE_URL",
        description="Primary jobs queue URL (hub producer, worker consumer).",
    )
    sqs_dlq_url: str | None = Field(
        default=None,
        validation_alias="SQS_DLQ_URL",
        description="Dead-letter queue URL for debugging failed messages (optional for hub).",
    )
    internal_service_token: str = Field(
        default="",
        validation_alias="INTERNAL_SERVICE_TOKEN",
        description="Bearer secret for ``/internal/*`` routes (must match agent ``HUB_SERVICE_TOKEN``).",
    )

    # --- Gmail Pub/Sub + OAuth (operator / hub — see gmail-pubsub-implementation.md) ---
    hub_public_url: str = Field(
        default="http://127.0.0.1:8000",
        validation_alias="HUB_PUBLIC_URL",
        description="Public hub base URL for OAuth redirects (no trailing path).",
    )
    gmail_pubsub_topic: str = Field(
        default="",
        validation_alias="GMAIL_PUBSUB_TOPIC",
        description='Format: projects/{GCP_PROJECT}/topics/agent-hub-gmail-push',
    )
    gmail_oauth_client_id: str = Field(default="", validation_alias="GMAIL_OAUTH_CLIENT_ID")
    gmail_oauth_client_secret: str = Field(default="", validation_alias="GMAIL_OAUTH_CLIENT_SECRET")
    slack_oauth_client_id: str = Field(default="", validation_alias="SLACK_OAUTH_CLIENT_ID")
    slack_oauth_client_secret: str = Field(default="", validation_alias="SLACK_OAUTH_CLIENT_SECRET")
    gmail_webhook_secret: str = Field(default="", validation_alias="GMAIL_WEBHOOK_SECRET")
    gcp_project_id: str = Field(default="", validation_alias="GCP_PROJECT_ID")
    incident_triage_agent_url: str = Field(
        default="",
        validation_alias="INCIDENT_TRIAGE_AGENT_URL",
        description="Worker calls this base URL when no live Deployment.base_url (local dev).",
    )
    gmail_renewal_scheduler_seconds: int = Field(
        default=86_400,
        ge=0,
        validation_alias="GMAIL_RENEWAL_SCHEDULER_SECONDS",
        description="Worker background loop: enqueue gmail_watch_renewal for expiring integrations; 0 disables.",
    )
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        validation_alias="LANGFUSE_HOST",
        description="Public Langfuse UI base URL for dashboard trace links (no secrets).",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def async_database_url(self) -> str:
        """URL passed to `create_async_engine` — always uses the asyncpg driver prefix."""
        if self.database_url is not None:
            url = str(self.database_url)
            if url.startswith("postgresql+asyncpg://"):
                return url
            if url.startswith("postgresql://"):
                return url.replace("postgresql://", "postgresql+asyncpg://", 1)
            if url.startswith("postgres://"):
                return url.replace("postgres://", "postgresql+asyncpg://", 1)
            return url
        return (
            f"postgresql+asyncpg://{self.database_user}:{self.database_password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
        )


@lru_cache
def get_settings() -> Settings:
    """Process-wide singleton; call `get_settings.cache_clear()` in tests if you mutate env."""
    return Settings()
