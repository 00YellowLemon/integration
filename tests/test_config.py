"""Unit tests for configuration management (app/core/config.py)."""
import os
import pathlib
import pytest
from pydantic import ValidationError
from app.core.config import Settings, get_settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()


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
    assert settings.APP_HOST == "0.0.0.0"
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


# ==============================================================================
# Phase 5: Cloud Run Runtime Contract Tests (Task Group 1 / Validation 2.1)
# ==============================================================================

def _make_base_settings(**kwargs) -> Settings:
    """Return a minimal valid Settings instance with test credentials."""
    defaults = dict(
        GOOGLE_CLIENT_ID="test-id",
        GOOGLE_CLIENT_SECRET="test-secret",
        APP_SECRET_KEY="test-secret-key-32-bytes-long-abc",
        DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/db",
    )
    defaults.update(kwargs)
    return Settings(**defaults)


def test_port_env_var_takes_precedence_over_app_port():
    """Cloud Run injects PORT; effective_port must prefer it over APP_PORT."""
    settings = _make_base_settings(APP_PORT=8000)
    old_env = os.environ.pop("PORT", None)
    try:
        os.environ["PORT"] = "8080"
        assert settings.effective_port == 8080
    finally:
        if old_env is None:
            os.environ.pop("PORT", None)
        else:
            os.environ["PORT"] = old_env


def test_app_port_used_when_port_env_absent():
    """When PORT is not set, effective_port falls back to APP_PORT (local dev default)."""
    settings = _make_base_settings(APP_PORT=8000)
    old_env = os.environ.pop("PORT", None)
    try:
        assert settings.effective_port == 8000
    finally:
        if old_env is not None:
            os.environ["PORT"] = old_env


def test_effective_port_custom_app_port_fallback():
    """Verify APP_PORT=9000 is returned when PORT env var is absent."""
    settings = _make_base_settings(APP_PORT=9000)
    old_env = os.environ.pop("PORT", None)
    try:
        assert settings.effective_port == 9000
    finally:
        if old_env is not None:
            os.environ["PORT"] = old_env


def test_app_host_defaults_to_all_interfaces():
    """Cloud Run requires 0.0.0.0 binding; APP_HOST must default to 0.0.0.0."""
    settings = _make_base_settings()
    assert settings.APP_HOST == "0.0.0.0"


def test_procfile_exists_at_repository_root():
    """Procfile must exist at the repository root with the correct web process entry."""
    procfile = REPO_ROOT / "Procfile"
    assert procfile.exists(), "Procfile not found at repository root"
    content = procfile.read_text(encoding="utf-8").strip()
    assert content == "web: uvicorn app.main:app --host 0.0.0.0 --port $PORT", (
        f"Procfile content does not match expected Cloud Run entry. Got:\n{content}"
    )


def test_gcloudignore_exists_and_excludes_sensitive_paths():
    """.gcloudignore must exist and exclude key dev-only / sensitive paths."""
    gcloudignore = REPO_ROOT / ".gcloudignore"
    assert gcloudignore.exists(), ".gcloudignore not found at repository root"
    content = gcloudignore.read_text(encoding="utf-8")
    for pattern in [".venv/", ".env", "tests/", "__pycache__/", ".git/"]:
        assert pattern in content, f"Expected '{pattern}' in .gcloudignore but it was missing."
