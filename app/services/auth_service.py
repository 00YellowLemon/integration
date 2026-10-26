"""Authentication service managing OAuth credentials lifecycle and automatic token refresh."""
from datetime import datetime, timedelta, timezone
from typing import Optional
import google.auth.transport.requests
from google.oauth2.credentials import Credentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.account import Account
from app.services.token_store import TokenStore

READONLY_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
]


class AuthService:
    """Service providing valid, refreshed Google OAuth 2.0 Credentials."""

    @staticmethod
    async def get_valid_credentials(
        db: AsyncSession,
        email: Optional[str] = None,
    ) -> Credentials:
        """
        Retrieve and ensure valid Google credentials for an active account.
        If the access token is expired or within 5 minutes of expiring, it is
        automatically refreshed and updated in the database.
        """
        settings = get_settings()
        account = await TokenStore.get_active_account(db, email=email)
        if not account:
            raise ValueError("No active Google account connected. Please authenticate via /auth/login first.")

        tokens = TokenStore.get_decrypted_tokens(account)

        # Ensure account token expiry is treated as timezone-aware UTC
        account_expiry = account.token_expiry
        if account_expiry and account_expiry.tzinfo is None:
            account_expiry = account_expiry.replace(tzinfo=timezone.utc)

        # Naive UTC datetime for google-auth compatibility
        expiry_naive = (
            account_expiry.astimezone(timezone.utc).replace(tzinfo=None)
            if account_expiry
            else None
        )

        creds = Credentials(
            token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET.get_secret_value(),
            scopes=READONLY_SCOPES,
            expiry=expiry_naive,
        )

        now_utc = datetime.now(timezone.utc)
        # Check if expired or within 300 seconds (5 minutes) of expiry
        token_is_expiring = (
            account_expiry is None
            or account_expiry <= (now_utc + timedelta(seconds=300))
            or creds.expired
        )

        if token_is_expiring:
            request = google.auth.transport.requests.Request()
            creds.refresh(request)

            new_expiry = (
                creds.expiry.replace(tzinfo=timezone.utc)
                if creds.expiry and creds.expiry.tzinfo is None
                else (now_utc + timedelta(seconds=3600))
            )

            await TokenStore.update_tokens(
                db=db,
                account_id=account.id,
                access_token=creds.token,
                expiry=new_expiry,
            )

        return creds
