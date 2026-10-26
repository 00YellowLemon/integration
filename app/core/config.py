"""Application configuration using Pydantic Settings."""
import logging
from functools import lru_cache
from typing import Optional
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("gmail_assistant.config")


class Settings(BaseSettings):
    """Application configuration settings loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    GOOGLE_CLIENT_ID: str = Field(..., description="Google OAuth 2.0 Web Client ID")
    GOOGLE_CLIENT_SECRET: SecretStr = Field(..., description="Google OAuth 2.0 Client Secret")
    GOOGLE_REDIRECT_URI: str = Field(
        default="http://localhost:8000/auth/callback",
        description="Google OAuth 2.0 registered redirect URI",
    )

    APP_SECRET_KEY: SecretStr = Field(
        ...,
        description="Application 32-byte secret key (hex or string) for AES-GCM and HMAC state generation",
    )

    DATABASE_URL: str = Field(
        ...,
        description="Async PostgreSQL connection string (e.g. postgresql+asyncpg://...)",
    )

    APP_HOST: str = Field(default="127.0.0.1", description="FastAPI server host")
    APP_PORT: int = Field(default=8000, description="FastAPI server port")
    LOG_LEVEL: str = Field(default="INFO", description="Application log level")

    GCP_PROJECT_ID: Optional[str] = Field(
        default=None,
        description="Google Cloud Project ID (auto-discovered from ADC if omitted)",
    )
    PUBSUB_TOPIC_NAME: str = Field(
        default="gmail-notifications",
        description="Pub/Sub topic name for Gmail push notifications",
    )
    PUBSUB_SUBSCRIPTION_NAME: str = Field(
        default="gmail-notifications-sub",
        description="Pub/Sub subscription name",
    )

    # ------------------------------------------------------------------
    # Phase 3: Vertex AI / Gemini triage agent configuration (ADC-only, NO API KEY)
    # ------------------------------------------------------------------
    GOOGLE_CLOUD_LOCATION: str = Field(
        default="global",
        description="Vertex AI location; 'global' is the Vertex global endpoint",
    )
    GOOGLE_GENAI_USE_VERTEXAI: bool = Field(
        default=True,
        description="Explicit backend-selection flag for the google-genai SDK",
    )
    TRIAGE_ENABLED: bool = Field(
        default=True,
        description="Master switch; when False no worker or agent is constructed",
    )
    TRIAGE_MODEL: str = Field(
        default="gemini-3.8-flash",
        description="Gemini model id used by the triage agent",
    )
    TRIAGE_TEMPERATURE: float = Field(
        default=0.1,
        description="Low temperature for deterministic analysis (Gemini 3 default is 1.0)",
    )
    TRIAGE_MAX_BODY_CHARS: int = Field(
        default=12000,
        description="Maximum characters of email body text surfaced to the model",
    )
    TRIAGE_MAX_MESSAGES_PER_THREAD: int = Field(
        default=10,
        description="Cost guardrail: maximum messages triaged per thread run",
    )
    TRIAGE_MAX_RETRIES: int = Field(
        default=2,
        description="Bounded retry count passed to the chat model",
    )


    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        """Ensure standard postgres URLs use asyncpg driver if postgresql:// is provided."""
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        if v.startswith("postgresql://") and not v.startswith("postgresql+"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    @property
    def database_url_unmasked(self) -> str:
        """Returns the unmasked database URL for SQLAlchemy engine creation."""
        return self.DATABASE_URL

    def resolve_gcp_project(self) -> Optional[str]:
        """
        Resolve the effective GCP project id for Vertex AI and Pub/Sub.

        Returns the explicit GCP_PROJECT_ID when set, otherwise falls back to
        Application Default Credentials (`google.auth.default()`) discovery.
        Returns None when no project can be resolved (e.g. no ADC available),
        in which case triage stays disabled but the app still boots.
        """
        if self.GCP_PROJECT_ID:
            return self.GCP_PROJECT_ID
        try:
            import google.auth

            _, project = google.auth.default()
            return project
        except Exception as e:
            logger.debug("Could not resolve GCP project from ADC: %s", e)
            return None

    @property
    def triage_ready(self) -> bool:
        """True when triage is enabled and a GCP project can be resolved."""
        return bool(self.TRIAGE_ENABLED and self.resolve_gcp_project() is not None)

    @property
    def database_is_postgres(self) -> bool:
        """True when DATABASE_URL points at PostgreSQL (used to gate the checkpointer)."""
        url = self.DATABASE_URL.lower()
        return url.startswith("postgresql://") or url.startswith("postgres://")


@lru_cache()
def get_settings() -> Settings:
    """Retrieve cached singleton Settings instance."""
    return Settings()
