"""Token storage service for saving, retrieving, and updating encrypted OAuth credentials in Supabase."""
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decrypt_token, encrypt_token
from app.models.account import Account


class TokenStore:
    """Service handling encrypted persistence and retrieval of OAuth tokens."""

    @staticmethod
    async def save_tokens(
        db: AsyncSession,
        email: str,
        access_token: str,
        refresh_token: str,
        expiry: datetime,
    ) -> Account:
        """
        Save or update OAuth credentials for an email address.
        Tokens are encrypted before being written to PostgreSQL.
        """
        settings = get_settings()
        secret_key = settings.APP_SECRET_KEY.get_secret_value()

        enc_access = encrypt_token(access_token, secret_key)
        enc_refresh = encrypt_token(refresh_token, secret_key) if refresh_token else None

        # Check for existing account by email
        result = await db.execute(select(Account).where(Account.email == email))
        account = result.scalar_one_or_none()

        now = datetime.now(timezone.utc)
        if account:
            account.encrypted_access_token = enc_access
            if enc_refresh is not None:
                account.encrypted_refresh_token = enc_refresh
            account.token_expiry = expiry
            account.is_active = True
            account.updated_at = now
        else:
            if enc_refresh is None:
                raise ValueError("Refresh token is required when creating a new account record")
            account = Account(
                id=uuid.uuid4(),
                email=email,
                encrypted_access_token=enc_access,
                encrypted_refresh_token=enc_refresh,
                token_expiry=expiry,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            db.add(account)

        await db.commit()
        await db.refresh(account)
        return account

    @staticmethod
    async def get_active_account(
        db: AsyncSession,
        email: Optional[str] = None,
    ) -> Optional[Account]:
        """
        Retrieve an active Account. If email is not specified, returns the most recently updated active account.
        """
        stmt = select(Account).where(Account.is_active.is_(True))
        if email:
            stmt = stmt.where(Account.email == email)
        else:
            stmt = stmt.order_by(Account.updated_at.desc())

        result = await db.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def update_tokens(
        db: AsyncSession,
        account_id: uuid.UUID,
        access_token: str,
        expiry: datetime,
    ) -> None:
        """
        Update the access token and expiry after an automated refresh cycle.
        """
        settings = get_settings()
        secret_key = settings.APP_SECRET_KEY.get_secret_value()
        enc_access = encrypt_token(access_token, secret_key)

        result = await db.execute(select(Account).where(Account.id == account_id))
        account = result.scalar_one_or_none()
        if not account:
            raise ValueError(f"Account with id {account_id} not found")

        account.encrypted_access_token = enc_access
        account.token_expiry = expiry
        account.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(account)

    @staticmethod
    def get_decrypted_tokens(account: Account) -> dict:
        """
        Decrypt and return the access_token and refresh_token from an Account model.
        """
        settings = get_settings()
        secret_key = settings.APP_SECRET_KEY.get_secret_value()

        access_token = decrypt_token(account.encrypted_access_token, secret_key)
        refresh_token = decrypt_token(account.encrypted_refresh_token, secret_key)

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
        }
