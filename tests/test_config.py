"""Unit tests for configuration management (app/core/config.py)."""
import os
import pytest
from pydantic import ValidationError
from app.core.config import Settings, get_settings


def test_valid_settings_loading():
    """Verify that settings load all fields correctly and secrets are masked."""
    settings = Settings(
        GOOGLE_CLIENT_ID="test-id",
        GOOGLE_CLIENT_SECRET="test-secret",
        GOOGLE_REDIRECT_URI="http://localhost:8000/auth/callback",
        APP_SECRET_KEY="test-secret-key-32-bytes-long-abc",
        DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/db",
        LOG_LEVEL="INFO",
    )

    assert settings.GOOGLE_CLIENT_ID == "test-id"
    assert settings.GOOGLE_CLIENT_SECRET.get_secret_value() == "test-secret"
    assert "test-secret" not in str(settings.GOOGLE_CLIENT_SECRET)
    assert settings.APP_SECRET_KEY.get_secret_value() == "test-secret-key-32-bytes-long-abc"
    assert settings.APP_HOST == "127.0.0.1"
    assert settings.APP_PORT == 8000
    assert settings.LOG_LEVEL == "INFO"


def test_missing_settings_raises_validation_error():
    """Verify that omitting required settings raises pydantic.ValidationError."""
    # Temporarily remove required environment variables
    old_env = os.environ.copy()
    try:
        os.environ.clear()
        with pytest.raises(ValidationError) as exc_info:
            Settings(_env_file=None)
        errors = exc_info.value.errors()
        missing_fields = {e["loc"][0] for e in errors}
        assert "GOOGLE_CLIENT_ID" in missing_fields
        assert "GOOGLE_CLIENT_SECRET" in missing_fields
        assert "APP_SECRET_KEY" in missing_fields
        assert "DATABASE_URL" in missing_fields
    finally:
        os.environ.update(old_env)


def test_database_url_normalization():
    """Verify postgresql:// and postgres:// URLs are converted to postgresql+asyncpg://."""
    s1 = Settings(
        GOOGLE_CLIENT_ID="id",
        GOOGLE_CLIENT_SECRET="sec",
        APP_SECRET_KEY="key",
        DATABASE_URL="postgres://user:pass@host:5432/db",
    )
    assert s1.DATABASE_URL == "postgresql+asyncpg://user:pass@host:5432/db"

    s2 = Settings(
        GOOGLE_CLIENT_ID="id",
        GOOGLE_CLIENT_SECRET="sec",
        APP_SECRET_KEY="key",
        DATABASE_URL="postgresql://user:pass@host:5432/db",
    )
    assert s2.DATABASE_URL == "postgresql+asyncpg://user:pass@host:5432/db"

    s3 = Settings(
        GOOGLE_CLIENT_ID="id",
        GOOGLE_CLIENT_SECRET="sec",
        APP_SECRET_KEY="key",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
    )
    assert s3.DATABASE_URL == "sqlite+aiosqlite:///:memory:"


def test_cached_settings_singleton():
    """Verify get_settings returns the singleton instance."""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
