"""Interactive Verification CLI Script for Google OAuth 2.0 & Supabase Integration.

Run: python scripts/verify_oauth.py [--poll] [--test-refresh]
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import sys

from sqlalchemy import select, text

# Add workspace root to sys.path
sys.path.insert(0, ".")

from app.core.config import get_settings
from app.core.security import generate_oauth_state
from app.db.session import get_db, get_engine, init_db
from app.models.account import Account
from app.services.auth_service import AuthService, READONLY_SCOPES
from app.services.gmail import GmailClient
from app.services.token_store import TokenStore
from google_auth_oauthlib.flow import Flow


def print_banner():
    print("\n" + "=" * 70)
    print("      GMAIL AI ASSISTANT - OAUTH & SUPABASE VERIFICATION CLI")
    print("=" * 70 + "\n")


async def verify_environment():
    print("[1/4] Checking Environment Configuration...")
    try:
        settings = get_settings()
        print(f"  [OK] GOOGLE_CLIENT_ID:     {settings.GOOGLE_CLIENT_ID[:12]}...")
        print(f"  [OK] GOOGLE_REDIRECT_URI:  {settings.GOOGLE_REDIRECT_URI}")
        print(f"  [OK] APP_SECRET_KEY:       [Configured: {len(settings.APP_SECRET_KEY.get_secret_value())} chars]")
        print(f"  [OK] DATABASE_URL:         {settings.DATABASE_URL.split('@')[-1] if '@' in settings.DATABASE_URL else 'Configured'}")
        return settings
    except Exception as e:
        print(f"  [FAIL] Configuration Error: {e}")
        print("\nPlease ensure your .env file is created with all required keys from .env.example.")
        return None


async def verify_database():
    print("\n[2/4] Verifying Supabase Database Connection & Schema...")
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            res = await conn.execute(text("SELECT 1"))
            assert res.scalar() == 1
        print("  [OK] Successfully connected to PostgreSQL / Supabase.")
        await init_db()
        print("  [OK] Schema tables verified (`accounts` table ready).")
        return True
    except Exception as e:
        print(f"  [FAIL] Database Connection Failed: {e}")
        return False


async def check_account_and_mailbox(poll: bool = False, test_refresh: bool = False):
    print("\n[3/4] Inspecting Stored OAuth Accounts...")
    settings = get_settings()

    async for db in get_db():
        account = await TokenStore.get_active_account(db)
        if not account and poll:
            print("  No active account found. Waiting for authentication...")
            state = generate_oauth_state(settings.APP_SECRET_KEY.get_secret_value())
            flow = Flow.from_client_config(
                client_config={
                    "web": {
                        "client_id": settings.GOOGLE_CLIENT_ID,
                        "client_secret": settings.GOOGLE_CLIENT_SECRET.get_secret_value(),
                        "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                    }
                },
                scopes=READONLY_SCOPES,
                redirect_uri=settings.GOOGLE_REDIRECT_URI,
                state=state,
            )
            auth_url, _ = flow.authorization_url(
                access_type="offline",
                prompt="consent",
                include_granted_scopes="true",
            )
            print(f"\n  --> Open this link in your browser to authorize:")
            print(f"     {auth_url}\n")
            print("  Polling database every 3 seconds (Press Ctrl+C to cancel)...")
            for _ in range(40):
                await asyncio.sleep(3)
                account = await TokenStore.get_active_account(db)
                if account:
                    break

        if not account:
            state = generate_oauth_state(settings.APP_SECRET_KEY.get_secret_value())
            flow = Flow.from_client_config(
                client_config={
                    "web": {
                        "client_id": settings.GOOGLE_CLIENT_ID,
                        "client_secret": settings.GOOGLE_CLIENT_SECRET.get_secret_value(),
                        "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                    }
                },
                scopes=READONLY_SCOPES,
                redirect_uri=settings.GOOGLE_REDIRECT_URI,
                state=state,
            )
            auth_url, _ = flow.authorization_url(
                access_type="offline",
                prompt="consent",
                include_granted_scopes="true",
            )
            print("  [INFO] No active OAuth account currently stored.")
            print(f"\n  To authorize, run the server and visit:")
            print(f"     http://{settings.APP_HOST}:{settings.APP_PORT}/auth/login?redirect=true")
            print(f"  Or directly:")
            print(f"     {auth_url}\n")
            return

        print(f"  [OK] Found Active Account:")
        print(f"    - Email:         {account.email}")
        print(f"    - ID:            {account.id}")
        print(f"    - Token Expiry:  {account.token_expiry}")
        print(f"    - History ID:    {account.history_id or 'None'}")

        if test_refresh:
            print("\n  [Testing Auto-Refresh Cycle]")
            print("  Artificially expiring token in database (setting expiry = now - 1 hour)...")
            account.token_expiry = datetime.now(timezone.utc) - timedelta(hours=1)
            await db.commit()

        print("\n[4/4] Verifying Gmail REST API Connection (users.getProfile)...")
        try:
            creds = await AuthService.get_valid_credentials(db, email=account.email)
            print("  [OK] OAuth credentials validated (auto-refreshed if needed).")
            if test_refresh:
                await db.refresh(account)
                print(f"  [OK] Verified updated token_expiry: {account.token_expiry}")

            client = GmailClient(credentials=creds)
            profile = await client.get_profile(user_id="me")

            print("\n" + "-" * 50)
            print("        GMAIL MAILBOX PROFILE STATISTICS")
            print("-" * 50)
            print(f"  Email Address:    {profile.get('emailAddress')}")
            print(f"  Total Messages:   {profile.get('messagesTotal'):,}")
            print(f"  Total Threads:    {profile.get('threadsTotal'):,}")
            print(f"  History ID:       {profile.get('historyId')}")
            print("-" * 50)
            print("\n*** MILESTONE 1 VERIFICATION SUCCESSFUL! All checks passed. ***\n")
        except Exception as e:
            print(f"  [FAIL] Failed to query Gmail API: {e}")
        break


async def main():
    parser = argparse.ArgumentParser(description="Verify Gmail OAuth and Supabase setup.")
    parser.add_argument("--poll", action="store_true", help="Poll for account after generating URL")
    parser.add_argument("--test-refresh", action="store_true", help="Simulate expired token and test auto-refresh")
    args = parser.parse_args()

    print_banner()
    settings = await verify_environment()
    if not settings:
        sys.exit(1)

    db_ok = await verify_database()
    if not db_ok:
        sys.exit(1)

    await check_account_and_mailbox(poll=args.poll, test_refresh=args.test_refresh)


if __name__ == "__main__":
    asyncio.run(main())
