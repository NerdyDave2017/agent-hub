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
        validation_alias=AliasChoices("HUB_PUBLIC_URL", "HUB_BASE_URL"),
        description="Public hub base URL (OAuth, agent runtime env AGENT_HUB_PUBLIC_URL). ECS often sets HUB_BASE_URL.",
    )
    gmail_pubsub_topic: str = Field(
        default="",
        validation_alias="GMAIL_PUBSUB_TOPIC",
        description='Format: projects/{GCP_PROJECT}/topics/agent-hub-gmail-push',
    )
    gmail_oauth_client_id: str = Field(default="", validation_alias="GMAIL_OAUTH_CLIENT_ID")
    gmail_oauth_client_secret: str = Field(default="", validation_alias="GMAIL_OAUTH_CLIENT_SECRET")
    google_signin_client_id: str = Field(
        default="",
        validation_alias="GOOGLE_SIGNIN_CLIENT_ID",
        description="Google OAuth client ID for frontend Sign-In button; verified server-side via ID token.",
    )
    slack_oauth_client_id: str = Field(default="", validation_alias="SLACK_OAUTH_CLIENT_ID")
    slack_oauth_client_secret: str = Field(default="", validation_alias="SLACK_OAUTH_CLIENT_SECRET")
    gmail_webhook_secret: str = Field(default="", validation_alias="GMAIL_WEBHOOK_SECRET")
    gcp_project_id: str = Field(default="", validation_alias="GCP_PROJECT_ID")
    incident_triage_agent_url: str = Field(
        default="",
        validation_alias="INCIDENT_TRIAGE_AGENT_URL",
        description="Worker calls this base URL when no live Deployment.base_url (local dev).",
    )

    # --- Worker: App Runner CreateService (optional; Terraform supplies ARNs + ECR image) ---
    app_runner_create_access_role_arn: str = Field(
        default="",
        validation_alias=AliasChoices(
            "APP_RUNNER_CREATE_ACCESS_ROLE_ARN",
            "AGENT_ECR_ACCESS_ROLE_ARN",
        ),
        description="IAM role App Runner assumes to pull from ECR (Terraform worker outputs AGENT_ECR_ACCESS_ROLE_ARN).",
    )
    app_runner_create_instance_role_arn: str = Field(
        default="",
        validation_alias=AliasChoices(
            "APP_RUNNER_CREATE_INSTANCE_ROLE_ARN",
            "AGENT_INSTANCE_ROLE_ARN",
        ),
        description="Instance role for the agent container (Terraform worker outputs AGENT_INSTANCE_ROLE_ARN).",
    )
    app_runner_create_image_identifier: str = Field(
        default="",
        validation_alias="APP_RUNNER_CREATE_IMAGE_IDENTIFIER",
        description="ECR image URI and tag, e.g. 123456789012.dkr.ecr.us-east-1.amazonaws.com/agent:1.0.0",
    )
    app_runner_create_port: str = Field(default="8080", validation_alias="APP_RUNNER_CREATE_PORT")
    app_runner_create_cpu: str = Field(default="1024", validation_alias="APP_RUNNER_CREATE_CPU")
    app_runner_create_memory: str = Field(default="2048", validation_alias="APP_RUNNER_CREATE_MEMORY")
    app_runner_create_health_check_path: str = Field(
        default="/health",
        validation_alias="APP_RUNNER_CREATE_HEALTH_CHECK_PATH",
    )
    app_runner_create_auto_deployments_enabled: bool = Field(
        default=False,
        validation_alias="APP_RUNNER_CREATE_AUTO_DEPLOYMENTS_ENABLED",
    )
    app_runner_create_auto_scaling_configuration_arn: str = Field(
        default="",
        validation_alias="APP_RUNNER_CREATE_AUTO_SCALING_CONFIGURATION_ARN",
    )
    app_runner_create_vpc_connector_arn: str = Field(
        default="",
        validation_alias="APP_RUNNER_CREATE_VPC_CONNECTOR_ARN",
        description="When set, egress uses this VPC connector (private RDS, etc.).",
    )
    gmail_renewal_scheduler_seconds: int = Field(
        default=86_400,
        ge=0,
        validation_alias="GMAIL_RENEWAL_SCHEDULER_SECONDS",
        description="Worker background loop: enqueue gmail_watch_renewal for expiring integrations; 0 disables.",
    )
    metrics_rollup_scheduler_seconds: int = Field(
        default=3_600,
        ge=0,
        validation_alias="METRICS_ROLLUP_SCHEDULER_SECONDS",
        description="Worker background loop: enqueue metrics_rollup for each active agent for the previous UTC hour; 0 disables.",
    )
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        validation_alias="LANGFUSE_HOST",
        description="Public Langfuse UI base URL for dashboard trace links (no secrets).",
    )
    langfuse_public_key: str = Field(
        default="",
        validation_alias="LANGFUSE_PUBLIC_KEY",
        description="Langfuse public API key — optional; worker metrics rollup calls Langfuse Metrics API when set with LANGFUSE_SECRET_KEY.",
    )
    langfuse_secret_key: str = Field(
        default="",
        validation_alias="LANGFUSE_SECRET_KEY",
        description="Langfuse secret API key — optional pair with LANGFUSE_PUBLIC_KEY for rollup enrichment.",
    )
    jwt_secret_key: str = Field(
        default="",
        validation_alias="JWT_SECRET_KEY",
        description="HS256 secret for hub-issued access tokens (dashboard + auth/login).",
    )
    jwt_algorithm: str = Field(default="HS256", validation_alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=60, ge=5, le=24 * 60, validation_alias="JWT_EXPIRE_MINUTES")
    jwt_issuer: str = Field(default="agent-hub", validation_alias="JWT_ISSUER")

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
