"""Tests for Pub/Sub push webhook receiver and manual sync/watch endpoints."""
import base64
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import AsyncMock, patch
import pytest

from app.services.ingestion import SyncStats
from app.services.token_store import TokenStore


def _encode_pubsub_data(payload: dict) -> str:
    """Encode dictionary into base64 string for Pub/Sub data field."""
    raw = json.dumps(payload).encode("utf-8")
    return base64.b64encode(raw).decode("utf-8")


@pytest.fixture
async def active_account(test_session):
    """Seed an active Google account for API endpoint tests."""
    account = await TokenStore.save_tokens(
        db=test_session,
        email="user@gmail.com",
        access_token="mock-access-token",
        refresh_token="mock-refresh-token",
        expiry=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    account.history_id = "12300"
    await test_session.commit()
    await test_session.refresh(account)
    return account


@pytest.mark.asyncio
async def test_valid_pubsub_webhook(client, active_account):
    """Test valid Pub/Sub push payload triggers delta sync and returns 204 No Content."""
    data_b64 = _encode_pubsub_data({"emailAddress": "user@gmail.com", "historyId": "12345"})
    envelope = {
        "message": {
            "data": data_b64,
            "messageId": "pubsub_msg_999",
            "publishTime": "2026-10-23T12:00:00Z",
        },
        "subscription": "projects/my-proj/subscriptions/gmail-sub",
    }

    with patch("app.api.webhook.IngestionService.delta_sync", new_callable=AsyncMock) as mock_sync:
        mock_sync.return_value = SyncStats(
            account_email="user@gmail.com",
            threads_synced=1,
            messages_synced=2,
            new_history_id="12345",
        )
        response = await client.post("/api/v1/webhook/pubsub", json=envelope)

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_malformed_pubsub_payload_missing_data(client):
    """Test Pub/Sub push request with missing message.data returns 400 Bad Request."""
    envelope = {
        "message": {
            "messageId": "pubsub_msg_empty",
        }
    }
    response = await client.post("/api/v1/webhook/pubsub", json=envelope)
    assert response.status_code == 400
    assert "Missing 'data'" in response.json().get("detail", "")


@pytest.mark.asyncio
async def test_malformed_pubsub_payload_invalid_json(client):
    """Test Pub/Sub payload with non-JSON message data returns 400 Bad Request."""
    invalid_b64 = base64.b64encode(b"not valid json").decode("utf-8")
    envelope = {
        "message": {
            "data": invalid_b64,
        }
    }
    response = await client.post("/api/v1/webhook/pubsub", json=envelope)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_nonexistent_account_webhook(client):
    """Test Pub/Sub notification for unknown email address returns 404 Not Found."""
    data_b64 = _encode_pubsub_data({"emailAddress": "unknown@example.com", "historyId": "11111"})
    envelope = {
        "message": {
            "data": data_b64,
            "messageId": "pubsub_msg_unknown",
        }
    }
    response = await client.post("/api/v1/webhook/pubsub", json=envelope)
    assert response.status_code == 404
    assert "not found" in response.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_manual_sync_endpoint(client, active_account):
    """Test POST /api/v1/gmail/sync triggers sync and returns SyncResponse."""
    with patch("app.api.gmail.IngestionService.delta_sync", new_callable=AsyncMock) as mock_sync:
        mock_sync.return_value = SyncStats(
            account_email="user@gmail.com",
            threads_synced=3,
            messages_synced=5,
            new_history_id="12400",
        )
        response = await client.post("/api/v1/gmail/sync", json={"max_threads": 50})

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["account_email"] == "user@gmail.com"
    assert data["threads_synced"] == 3
    assert data["messages_synced"] == 5
    assert data["new_history_id"] == "12400"


@pytest.mark.asyncio
async def test_manual_sync_nonexistent_account(client):
    """Test POST /api/v1/gmail/sync returns 404 when no account exists."""
    response = await client.post("/api/v1/gmail/sync", json={"email": "nobody@example.com"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_watch_endpoint(client, active_account):
    """Test POST /api/v1/gmail/watch calls users.watch and returns WatchRegistrationResponse."""
    with patch("app.api.gmail.AuthService.get_valid_credentials", new_callable=AsyncMock), \
         patch("app.api.gmail.PubSubHelper.get_topic_path", return_value="projects/test-proj/topics/gmail-notifications"), \
         patch("app.api.gmail.GmailClient") as MockClient:
        instance = MockClient.return_value
        instance.register_watch = AsyncMock(return_value={
            "historyId": "12500",
            "expiration": "1729700000000",
        })

        response = await client.post("/api/v1/gmail/watch")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "active"
    assert data["history_id"] == "12500"
    assert data["expiration"] == "1729700000000"
