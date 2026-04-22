"""
Central configuration loaded from environment variables (and optional `.env`).

Why this exists
---------------
Keeping connection strings and flags in one `Settings` object avoids scattering
`os.environ[...]` calls across the codebase. Alembic (`db/migrations/env.py`) imports
the same module so migrations and the app agree on which database they target.

`DATABASE_URL` may be omitted in local dev: discrete `DATABASE_*` fields build a URL
that matches the repo’s `docker-compose.yml` Postgres defaults.

SQS / AWS (hub + worker, next wiring steps)
-----------------------------------------
* **Production:** leave `AWS_ENDPOINT_URL` unset; boto3 uses real AWS; credentials come
  from the task IAM role (or standard AWS env vars in CI).
* **Local (LocalStack):** set `AWS_ENDPOINT_URL` to the compose-published endpoint (see
  README). Dummy keys `test` / `test` are normal for LocalStack; **never** use those in prod.
* **`SQS_QUEUE_URL`** — full queue URL returned by `awslocal sqs get-queue-url` (or AWS
  console). The hub sends here; the worker long-polls the same URL.
"""

from functools import lru_cache

from pydantic import AliasChoices, Field, PostgresDsn, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
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

    # --- AWS / SQS (optional until hub/worker enqueue + consume are wired) ---
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
