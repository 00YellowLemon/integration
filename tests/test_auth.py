"""Unit and integration tests for authentication, CSRF security, and OAuth endpoints."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import (
    decrypt_token,
    encrypt_token,
    generate_oauth_state,
    verify_oauth_state,
)
from app.models.account import Account


def test_csrf_state_lifecycle():
    """Verify state generation, validation, expiration, and tampering detection."""
    key = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    state = generate_oauth_state(key)
    assert state is not None
    assert len(state.split(".")) == 3

    # Valid state
    assert verify_oauth_state(state, key, max_age_seconds=600) is True

    # Tampered state
    parts = state.split(".")
    tampered = f"{parts[0]}.{parts[1]}.badsignature"
    assert verify_oauth_state(tampered, key) is False

    # Expired state
    old_ts_hex = hex(int(datetime.now(timezone.utc).timestamp()) - 1000)[2:]
    import hashlib, hmac
    payload = f"{old_ts_hex}.{parts[1]}"
    from app.core.security import _derive_32byte_key
    sig = hmac.new(_derive_32byte_key(key), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    expired_state = f"{payload}.{sig}"
    assert verify_oauth_state(expired_state, key, max_age_seconds=600) is False

    # Invalid input
    assert verify_oauth_state("", key) is False
    assert verify_oauth_state("invalid.format", key) is False


def test_aes_gcm_token_encryption():
    """Verify AES-256-GCM token encryption, non-determinism, and decryption."""
    key = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    plain_token = "ya29.a0AfH6SMD_example_access_token_12345"

    cipher1 = encrypt_token(plain_token, key)
    cipher2 = encrypt_token(plain_token, key)

    # Nonce randomization ensures different ciphertexts for same input
    assert cipher1 != cipher2

    # Correct decryption
    decrypted = decrypt_token(cipher1, key)
    assert decrypted == plain_token

    # Wrong key raises ValueError
    wrong_key = "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"
    with pytest.raises(ValueError, match="Failed to decrypt token"):
        decrypt_token(cipher1, wrong_key)

    # Tampered ciphertext raises ValueError
    with pytest.raises(ValueError):
        decrypt_token("corrupted_cipher_text_payload", key)


@pytest.mark.asyncio
async def test_auth_login_json(client: AsyncClient):
    """Verify GET /auth/login returns valid authorization URL with required scopes."""
    response = await client.get("/auth/login")
    assert response.status_code == 200
    data = response.json()
    assert "auth_url" in data
    auth_url = data["auth_url"]
    assert "accounts.google.com/o/oauth2/v2/auth" in auth_url
    assert "gmail.readonly" in auth_url
    assert "userinfo.email" in auth_url
    assert "access_type=offline" in auth_url
    assert "prompt=consent" in auth_url
    assert "state=" in auth_url


@pytest.mark.asyncio
async def test_auth_login_redirect(client: AsyncClient):
    """Verify GET /auth/login?redirect=true returns HTTP 307 redirect."""
    response = await client.get("/auth/login?redirect=true", follow_redirects=False)
    assert response.status_code == 307
    assert "location" in response.headers
    assert "accounts.google.com" in response.headers["location"]


@pytest.mark.asyncio
async def test_auth_callback_missing_params(client: AsyncClient):
    """Verify GET /auth/callback returns HTTP 400 when code or state is missing."""
    resp1 = await client.get("/auth/callback")
    assert resp1.status_code == 400

    resp2 = await client.get("/auth/callback?code=testcode")
    assert resp2.status_code == 400


@pytest.mark.asyncio
async def test_auth_callback_oauth_error(client: AsyncClient):
    """Verify GET /auth/callback returns HTTP 400 when error param is present."""
    response = await client.get("/auth/callback?error=access_denied")
    assert response.status_code == 400
    assert "access_denied" in response.text


@pytest.mark.asyncio
async def test_auth_callback_invalid_state(client: AsyncClient):
    """Verify GET /auth/callback returns HTTP 403 on invalid CSRF state."""
    response = await client.get("/auth/callback?code=mock_code&state=invalid_csrf_state")
    assert response.status_code == 403
    assert "Invalid or expired" in response.text


@pytest.mark.asyncio
async def test_auth_callback_successful_exchange(
    client: AsyncClient,
    test_session: AsyncSession,
):
    """Verify successful OAuth code exchange and database persistence."""
    settings = get_settings()
    valid_state = generate_oauth_state(settings.APP_SECRET_KEY.get_secret_value())

    mock_credentials = MagicMock()
    mock_credentials.token = "mock-access-token-xyz"
    mock_credentials.refresh_token = "mock-refresh-token-xyz"
    mock_credentials.expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    with patch("app.api.auth._build_oauth_flow") as mock_flow_builder, \
         patch("app.api.auth.fetch_user_email", return_value="alex.test@gmail.com"):

        mock_flow = MagicMock()
        mock_flow.credentials = mock_credentials
        mock_flow_builder.return_value = mock_flow

        response = await client.get(
            f"/auth/callback?code=mock_auth_code_123&state={valid_state}"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["email"] == "alex.test@gmail.com"

        # Verify account persisted in database
        result = await test_session.execute(
            select(Account).where(Account.email == "alex.test@gmail.com")
        )
        account = result.scalar_one_or_none()
        assert account is not None
        assert account.email == "alex.test@gmail.com"
        assert account.is_active is True

        # Verify tokens stored encrypted
        secret_key = settings.APP_SECRET_KEY.get_secret_value()
        dec_access = decrypt_token(account.encrypted_access_token, secret_key)
        dec_refresh = decrypt_token(account.encrypted_refresh_token, secret_key)
        assert dec_access == "mock-access-token-xyz"
        assert dec_refresh == "mock-refresh-token-xyz"
