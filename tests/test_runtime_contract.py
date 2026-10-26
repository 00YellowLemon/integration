"""
Hermetic unit tests for the Cloud Run runtime contract (Task Group 4, Task 4.3).

Verifies:
- Port binding precedence (PORT env var > APP_PORT fallback).
- Cloud Run health probe returns correct JSON shape.
- Webhook payload compatibility under production-equivalent settings.
- No Dockerfile present in the repository (constitution mandate).
"""
import base64
import json
import os
import pathlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.services.ingestion import SyncStats
from app.services.token_store import TokenStore

REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**overrides) -> Settings:
    """Build a minimal valid Settings instance for contract assertions."""
    base = dict(
        GOOGLE_CLIENT_ID="test-id",
        GOOGLE_CLIENT_SECRET="test-secret",
        APP_SECRET_KEY="test-secret-key-32-bytes-long-abc",
        DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/db",
    )
    base.update(overrides)
    return Settings(**base)


def _encode_pubsub(payload: dict) -> str:
    """Encode a dict as a base64 Pub/Sub data field."""
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")


# ==============================================================================
# Port Binding Contract (Task 4.3 — PORT vs APP_PORT)
# ==============================================================================

def test_port_binding_prefers_port_env_var():
    """
    Cloud Run injects PORT at container startup.
    Settings.effective_port must return that value over APP_PORT.
    """
    settings = _make_settings(APP_PORT=8000)
    saved = os.environ.pop("PORT", None)
    try:
        os.environ["PORT"] = "8080"
        assert settings.effective_port == 8080, (
            "effective_port should prefer PORT env var (Cloud Run contract)"
        )
    finally:
        if saved is None:
            os.environ.pop("PORT", None)
        else:
            os.environ["PORT"] = saved


def test_port_binding_falls_back_to_app_port():
    """Without PORT env var, effective_port must fall back to APP_PORT."""
    settings = _make_settings(APP_PORT=8000)
    saved = os.environ.pop("PORT", None)
    try:
        assert settings.effective_port == 8000
    finally:
        if saved is not None:
            os.environ["PORT"] = saved


def test_app_host_binds_all_interfaces():
    """
    Cloud Run requires the process to bind 0.0.0.0.
    The default APP_HOST must NOT be 127.0.0.1 or localhost.
    """
    settings = _make_settings()
    assert settings.APP_HOST == "0.0.0.0", (
        "APP_HOST must default to 0.0.0.0 for Cloud Run compatibility"
    )


# ==============================================================================
# No Dockerfile Constitution Mandate
# ==============================================================================

def test_no_dockerfile_in_repository():
    """
    The constitution strictly prohibits a Dockerfile.
    Cloud Run must build exclusively from Procfile + Google Cloud Buildpacks.
    """
    dockerfile = REPO_ROOT / "Dockerfile"
    dockercompose = REPO_ROOT / "docker-compose.yml"
    dockercompose_yaml = REPO_ROOT / "docker-compose.yaml"
    assert not dockerfile.exists(), (
        "Dockerfile found at repository root — strictly prohibited by project constitution"
    )
    assert not dockercompose.exists(), (
        "docker-compose.yml found — strictly prohibited by project constitution"
    )
    assert not dockercompose_yaml.exists(), (
        "docker-compose.yaml found — strictly prohibited by project constitution"
    )


# ==============================================================================
# Cloud Run Health Probe Contract (Task 4.2)
# ==============================================================================

@pytest.mark.asyncio
async def test_health_endpoint_returns_ok_shape(client):
    """
    Cloud Run health probes target /health.
    Response must be HTTP 200 with {status: ok, database: <string>}.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.execute = AsyncMock()

    mock_engine = MagicMock()
    mock_engine.connect = MagicMock(return_value=mock_conn)

    with patch("app.main.get_engine", return_value=mock_engine):
        response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "database" in body
    assert body["database"] in ("connected", "disconnected")


@pytest.mark.asyncio
async def test_health_endpoint_reports_disconnected_on_db_error(client):
    """
    /health must return HTTP 200 with database=disconnected (not 500)
    even when the database is unreachable — Cloud Run must not restart on DB blips.
    """
    from unittest.mock import MagicMock, patch

    mock_engine = MagicMock()
    mock_engine.connect.side_effect = Exception("simulated connection refused")

    with patch("app.main.get_engine", return_value=mock_engine):
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["database"] == "disconnected"


# ==============================================================================
# Webhook Payload Contract Under Production Settings (Task 4.1)
# ==============================================================================

@pytest.fixture
async def prod_account(test_session):
    """Seed a registered account matching the Cloud Run production payload."""
    account = await TokenStore.save_tokens(
        db=test_session,
        email="prod@gmail.com",
        access_token="prod-access-token",
        refresh_token="prod-refresh-token",
        expiry=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    account.history_id = "55000"
    await test_session.commit()
    await test_session.refresh(account)
    return account


@pytest.mark.asyncio
async def test_webhook_valid_prod_pubsub_format(client, prod_account):
    """
    Verify that the canonical Google Cloud Pub/Sub push envelope
    (as sent in production) is accepted and returns HTTP 204.
    """
    envelope = {
        "message": {
            "data": _encode_pubsub({"emailAddress": "prod@gmail.com", "historyId": "55001"}),
            "messageId": "prod-pubsub-msg-001",
            "publishTime": "2026-10-26T07:00:00.000Z",
            "attributes": {},
        },
        "subscription": "projects/project-cd2fe81b-1cc9-4618-a35/subscriptions/gmail-notifications-sub",
    }

    with patch("app.api.webhook.IngestionService.delta_sync", new_callable=AsyncMock) as mock_sync:
        mock_sync.return_value = SyncStats(
            account_email="prod@gmail.com",
            threads_synced=2,
            messages_synced=4,
            new_history_id="55001",
        )
        response = await client.post("/api/v1/webhook/pubsub", json=envelope)

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_webhook_unregistered_account_returns_404(client):
    """
    Pub/Sub push for an email not registered in PostgreSQL must return 404.
    This is the application-layer guard replacing OIDC (deferred per decision D6).
    """
    envelope = {
        "message": {
            "data": _encode_pubsub(
                {"emailAddress": "unknown-prod@gmail.com", "historyId": "99999"}
            ),
            "messageId": "prod-pubsub-msg-002",
        },
        "subscription": "projects/project-cd2fe81b-1cc9-4618-a35/subscriptions/gmail-notifications-sub",
    }
    response = await client.post("/api/v1/webhook/pubsub", json=envelope)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_webhook_missing_data_field_returns_400(client):
    """Missing message.data field must return HTTP 400 — Pub/Sub will retry."""
    envelope = {
        "message": {
            "messageId": "prod-pubsub-msg-003",
        },
        "subscription": "projects/project-cd2fe81b-1cc9-4618-a35/subscriptions/gmail-notifications-sub",
    }
    response = await client.post("/api/v1/webhook/pubsub", json=envelope)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_webhook_malformed_base64_returns_400(client):
    """Corrupted base64 data must return HTTP 400."""
    envelope = {
        "message": {
            "data": "!!!not-valid-base64!!!",
            "messageId": "prod-pubsub-msg-004",
        },
    }
    response = await client.post("/api/v1/webhook/pubsub", json=envelope)
    assert response.status_code == 400
