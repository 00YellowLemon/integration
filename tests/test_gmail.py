"""Unit and integration tests for GmailClient, status endpoint, and token auto-refresh."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest
from google.oauth2.credentials import Credentials
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import encrypt_token
from app.models.account import Account
from app.services.auth_service import AuthService
from app.services.gmail import GmailClient
from app.services.token_store import TokenStore


def test_gmail_client_scope_safety():
    """Verify that GmailClient rejects credentials containing write scopes."""
    # Read-only scope succeeds
    readonly_creds = MagicMock(spec=Credentials)
    readonly_creds.scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
    client = GmailClient(readonly_creds)
    assert client is not None

    # Write scope raises PermissionError
    write_creds = MagicMock(spec=Credentials)
    write_creds.scopes = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
    ]
    with pytest.raises(PermissionError, match="Forbidden write scopes detected"):
        GmailClient(write_creds)


@pytest.mark.asyncio
async def test_gmail_client_get_profile():
    """Verify GmailClient.get_profile parses users.getProfile API response correctly."""
    creds = MagicMock(spec=Credentials)
    creds.scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
    client = GmailClient(creds)

    mock_profile_data = {
        "emailAddress": "user@gmail.com",
        "messagesTotal": 1420,
        "threadsTotal": 850,
        "historyId": "987654321",
    }

    with patch.object(client, "_sync_get_profile", return_value=mock_profile_data):
        profile = await client.get_profile(user_id="me")
        assert profile["emailAddress"] == "user@gmail.com"
        assert profile["messagesTotal"] == 1420
        assert profile["threadsTotal"] == 850
        assert profile["historyId"] == "987654321"


@pytest.mark.asyncio
async def test_gmail_status_endpoint_no_account(client: AsyncClient):
    """Verify GET /api/v1/gmail/status returns HTTP 404 when no account is connected."""
    response = await client.get("/api/v1/gmail/status")
    assert response.status_code == 404
    assert "No active Google account connected" in response.text


@pytest.mark.asyncio
async def test_gmail_status_endpoint_success(
    client: AsyncClient,
    test_session: AsyncSession,
):
    """Verify GET /api/v1/gmail/status returns HTTP 200 with profile stats."""
    settings = get_settings()
    secret_key = settings.APP_SECRET_KEY.get_secret_value()

    # Pre-populate active account
    account = Account(
        email="test.user@gmail.com",
        encrypted_access_token=encrypt_token("active-access-token", secret_key),
        encrypted_refresh_token=encrypt_token("active-refresh-token", secret_key),
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=2),
        is_active=True,
    )
    test_session.add(account)
    await test_session.commit()

    mock_profile = {
        "emailAddress": "test.user@gmail.com",
        "messagesTotal": 542,
        "threadsTotal": 310,
        "historyId": "11223344",
    }

    with patch("app.services.gmail.GmailClient.get_profile", return_value=mock_profile):
        response = await client.get("/api/v1/gmail/status")
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "test.user@gmail.com"
        assert data["messages_total"] == 542
        assert data["threads_total"] == 310
        assert data["history_id"] == "11223344"
        assert data["is_connected"] is True

        # Check that account history_id was updated in DB
        result = await test_session.execute(
            select(Account).where(Account.email == "test.user@gmail.com")
        )
        updated_acc = result.scalar_one()
        assert updated_acc.history_id == "11223344"


@pytest.mark.asyncio
async def test_token_auto_refresh_lifecycle(test_session: AsyncSession):
    """Verify expired token triggers auto-refresh and updates database record."""
    settings = get_settings()
    secret_key = settings.APP_SECRET_KEY.get_secret_value()

    expired_time = datetime.now(timezone.utc) - timedelta(hours=1)
    account = Account(
        email="refresh.test@gmail.com",
        encrypted_access_token=encrypt_token("old-expired-token", secret_key),
        encrypted_refresh_token=encrypt_token("valid-refresh-token", secret_key),
        token_expiry=expired_time,
        is_active=True,
    )
    test_session.add(account)
    await test_session.commit()
    await test_session.refresh(account)

    new_expiry_naive = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

    def mock_refresh(self, request):
        self.token = "newly-refreshed-access-token"
        self.expiry = new_expiry_naive

    with patch.object(Credentials, "refresh", new=mock_refresh):
        creds = await AuthService.get_valid_credentials(test_session, email="refresh.test@gmail.com")
        assert creds is not None

        # Verify DB was updated
        result = await test_session.execute(
            select(Account).where(Account.email == "refresh.test@gmail.com")
        )
        refreshed_acc = result.scalar_one()
        decrypted_new_token = TokenStore.get_decrypted_tokens(refreshed_acc)["access_token"]
        assert decrypted_new_token == "newly-refreshed-access-token"
        ref_expiry = (
            refreshed_acc.token_expiry
            if refreshed_acc.token_expiry.tzinfo
            else refreshed_acc.token_expiry.replace(tzinfo=timezone.utc)
        )
        assert ref_expiry > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_health_check_endpoint(client: AsyncClient):
    """Verify GET /health returns application status."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_root_endpoint(client: AsyncClient):
    """Verify GET / returns API metadata and documentation links."""
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Gmail AI Assistant API"
    assert "/docs" in data["docs_url"]
