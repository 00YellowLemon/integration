"""Interactive Verification CLI Script for Gmail Ingestion Pipeline & Supabase Persistence.

Run:
    python scripts/verify_ingestion.py [--backfill] [--max-threads 20] [--watch]
"""
import argparse
import asyncio
from datetime import datetime, timezone
import sys

from sqlalchemy import func, select, text

# Add workspace root to sys.path
sys.path.insert(0, ".")

from app.core.config import get_settings
from app.core.pubsub import PubSubHelper
from app.db.session import get_db, get_engine, init_db
from app.models.account import Account
from app.models.message import Message
from app.models.thread import Thread
from app.services.auth_service import AuthService
from app.services.gmail import GmailClient
from app.services.ingestion import IngestionService
from app.services.token_store import TokenStore


def print_banner():
    print("\n" + "=" * 75)
    print("      GMAIL AI ASSISTANT - INGESTION PIPELINE & PERSISTENCE VERIFICATION")
    print("=" * 75 + "\n")


async def verify_database():
    print("[1/4] Verifying Supabase Database Connection & Schema...")
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            res = await conn.execute(text("SELECT 1"))
            assert res.scalar() == 1
        print("  [OK] Successfully connected to PostgreSQL / Supabase.")
        await init_db()
        print("  [OK] Schema tables verified (`accounts`, `threads`, `messages`).")
        return True
    except Exception as e:
        print(f"  [FAIL] Database Connection Failed: {e}")
        return False


async def inspect_account(email: str = None) -> Account:
    print("\n[2/4] Inspecting Connected Account...")
    async for db in get_db():
        account = await TokenStore.get_active_account(db, email=email)
        if not account:
            print("  [FAIL] No active account found in database.")
            print("  Please connect an account first via /auth/login or run: python scripts/verify_oauth.py")
            return None
        print(f"  [OK] Connected Account: {account.email}")
        print(f"  [OK] History ID Checkpoint: {account.history_id or 'None (initial backfill needed)'}")
        print(f"  [OK] Token Expiration: {account.token_expiry}")
        return account


async def execute_sync(account: Account, force_backfill: bool = False, max_threads: int = 50):
    print("\n[3/4] Executing Mailbox Ingestion...")
    async for db in get_db():
        # Re-fetch account in current session
        current_account = await TokenStore.get_active_account(db, email=account.email)
        if not current_account:
            print("  [FAIL] Account no longer found.")
            return

        try:
            if force_backfill or not current_account.history_id:
                print(f"  Running initial backfill (targeting up to {max_threads} threads)...")
                stats = await IngestionService.initial_backfill(db, current_account, max_threads=max_threads)
            else:
                print(f"  Running delta sync from history checkpoint {current_account.history_id}...")
                stats = await IngestionService.delta_sync(db, current_account)

            print(f"  [OK] Sync Complete!")
            print(f"       Threads Synced:    {stats.threads_synced}")
            print(f"       Messages Synced:   {stats.messages_synced}")
            print(f"       New History ID:    {stats.new_history_id}")
        except Exception as e:
            print(f"  [FAIL] Ingestion failed: {e}")
            import traceback
            traceback.print_exc()


async def display_summary_table():
    print("\n[4/4] Summary of Stored Mailbox Data in Supabase:")
    async for db in get_db():
        thread_count = (await db.execute(select(func.count(Thread.id)))).scalar() or 0
        message_count = (await db.execute(select(func.count(Message.id)))).scalar() or 0

        print(f"\n  Total Threads in DB:  {thread_count}")
        print(f"  Total Messages in DB: {message_count}\n")

        stmt = select(Thread).order_by(Thread.last_message_at.desc().nullslast()).limit(5)
        threads = (await db.execute(stmt)).scalars().all()

        if not threads:
            print("  No threads currently recorded.")
            return

        print("  Latest 5 Ingested Conversation Threads:")
        print("  " + "-" * 70)
        for idx, t in enumerate(threads, 1):
            msg_count = (await db.execute(
                select(func.count(Message.id)).where(Message.thread_id == t.id)
            )).scalar() or 0

            subj = (t.subject or "(No Subject)")[:40]
            date_str = t.last_message_at.strftime("%Y-%m-%d %H:%M UTC") if t.last_message_at else "Unknown date"
            snippet = (t.snippet or "")[:50]

            print(f"  {idx}. [{date_str}] ({msg_count} msgs) {subj}")
            print(f"     Snippet: {snippet}...")
        print("  " + "-" * 70)


async def test_watch(account: Account):
    print("\n[Optional] Testing Gmail Pub/Sub Watch Registration...")
    try:
        topic_path = PubSubHelper.get_topic_path()
        print(f"  Target Topic: {topic_path}")
        async for db in get_db():
            creds = await AuthService.get_valid_credentials(db, email=account.email)
            client = GmailClient(credentials=creds)
            res = await client.register_watch(topic_name=topic_path)
            print(f"  [OK] Watch registered successfully!")
            print(f"       History ID:  {res.get('historyId')}")
            print(f"       Expiration:  {res.get('expiration')} ms")
    except Exception as e:
        print(f"  [WARN] Watch registration skipped or failed: {e}")


async def main():
    parser = argparse.ArgumentParser(description="Gmail Ingestion & Supabase Persistence Verifier")
    parser.add_argument("--backfill", action="store_true", help="Force full backfill instead of delta sync")
    parser.add_argument("--max-threads", type=int, default=50, help="Maximum threads to backfill (default: 50)")
    parser.add_argument("--email", type=str, default=None, help="Target specific account by email")
    parser.add_argument("--watch", action="store_true", help="Register Gmail Pub/Sub watch after sync")
    args = parser.parse_args()

    print_banner()

    if not await verify_database():
        sys.exit(1)

    account = await inspect_account(email=args.email)
    if not account:
        sys.exit(1)

    await execute_sync(account, force_backfill=args.backfill, max_threads=args.max_threads)
    await display_summary_table()

    if args.watch:
        await test_watch(account)

    print("\n[DONE] Verification completed successfully.\n")


if __name__ == "__main__":
    asyncio.run(main())
