"""Validated application configuration.

Reads from environment variables / a local .env file via pydantic-settings.
Never logs or echoes secret values - see get_dependency_status() below,
which reports only whether a credential is *present*, never its content.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# The one .env this project uses lives at the repo root (see AGENTS.md's
# Secrets convention) - computed from this file's own location so it's
# found regardless of which directory the server is started from, rather
# than depending on the caller's current working directory.
_REPO_ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class AppEnv(str, Enum):
    development = "development"
    staging = "staging"
    production = "production"


class Settings(BaseSettings):
    """Foundation-chapter settings only.

    Object storage settings were added once the real service existed and
    was verified (Chapter 5 Part B/round 3, ADR 0006) - adding them
    earlier would have let readiness/config code silently pretend
    something unverified was configured. The job queue has no settings
    here yet for the same reason: RabbitMQ exists (Chapter 5 Part C) but
    nothing in this backend talks to it - that's still open, tracked
    work, not done by adding these object-storage settings.
    """

    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: AppEnv = AppEnv.development
    log_level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")

    # Azure OpenAI - presence-only at this chapter. Real values live in .env
    # (gitignored, see AGENTS.md's Secrets convention). Never given a default
    # here that could mask a missing value, and never included in any log
    # or error message - see get_dependency_status().
    azure_openai_api_key: str | None = None
    azure_openai_api_base: str | None = None
    azure_openai_deployment_name: str | None = None
    azure_openai_embeddings_api_key: str | None = None
    azure_openai_embeddings_endpoint: str | None = None
    azure_openai_embeddings_deployment: str | None = None

    # Database (Chapter 5 Part A). The app connects only as the
    # restricted tenderpilot_app runtime role (see
    # infra/compose/init/01-roles-and-extension.sh) - never the bootstrap
    # superuser. No default password: absence means "not configured",
    # not "connect with an empty password".
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "tenderpilot"
    database_app_user: str = "tenderpilot_app"
    database_app_password: str | None = None

    # Object storage (Chapter 5's remaining "connect the API to storage"
    # work item, closed here - see ADR 0006). Field names deliberately
    # match the SEAWEEDFS_APP_ACCESS_KEY/SEAWEEDFS_APP_SECRET_KEY names
    # already used by infra/compose/secrets/seaweedfs-s3-identities.json
    # and .env.example, rather than inventing a second, parallel naming
    # scheme for the same credential. This is the tenderpilot_app
    # identity - restricted to Read/Write/List on quarantine and
    # Read/List only on documents (see docs/technical/local-setup.md) -
    # never the admin or validator credential. No default keys: absence
    # means "not configured".
    seaweedfs_endpoint_url: str = "http://127.0.0.1:8333"
    seaweedfs_app_access_key: str | None = None
    seaweedfs_app_secret_key: str | None = None


def get_settings() -> Settings:
    """Load and validate settings. Raises pydantic.ValidationError on bad
    config - deliberately not caught here, so a broken config fails loudly
    at startup rather than limping along with defaults."""
    return Settings()


def get_dependency_status(settings: Settings) -> dict[str, str]:
    """What's actually configured right now, from config alone - never
    what's "planned", and never anything requiring real I/O.

    The job queue has no settings yet, so it reports 'not_configured'.
    Azure OpenAI reports whether credentials are *present*, which is not
    the same claim as "validated working" - see docs/technical/stack.md,
    model validation is Chapter 13's job, not this one's.

    Database and object storage are deliberately NOT included here:
    unlike everything else in this function, knowing whether either is
    actually reachable requires a real network call, so those two live
    checks live in db.get_database_status() and storage.get_storage_status()
    respectively, merged in by the /health/ready route instead - this
    function never does I/O, and reporting "credentials_present" for
    either here would risk being read as "verified working" when it
    isn't.
    """
    azure_chat_present = bool(
        settings.azure_openai_api_key
        and settings.azure_openai_api_base
        and settings.azure_openai_deployment_name
    )
    azure_embeddings_present = bool(
        settings.azure_openai_embeddings_api_key
        and settings.azure_openai_embeddings_endpoint
        and settings.azure_openai_embeddings_deployment
    )
    return {
        "job_queue": "not_configured",
        "model_provider_chat": "credentials_present" if azure_chat_present else "not_configured",
        "model_provider_embeddings": "credentials_present"
        if azure_embeddings_present
        else "not_configured",
    }
