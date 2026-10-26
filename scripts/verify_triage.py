"""Interactive Verification CLI for the LangChain Email Triage Agent (Phase 3).

Run:
    python scripts/verify_triage.py --thread-id <GMAIL_THREAD_ID>
    python scripts/verify_triage.py --message-id <MESSAGE_UUID> --force

Verifies DB connectivity, lists connected accounts and untriaged message
counts, then triages a chosen thread/message with live Gemini via Vertex AI,
printing category, urgency, confidence, action items, and the thread briefing.
Exits non-zero on failure.
"""
import argparse
import asyncio
import sys

sys.path.insert(0, ".")

from sqlalchemy import func, select

from app.db.session import get_db, get_engine, init_db, get_session_maker
from app.models.account import Account
from app.models.analysis import ActionItem, EmailAnalysis
from app.models.message import Message
from app.models.thread import Thread
from app.services.token_store import TokenStore
from app.triage.service import TriageService


def banner():
    print("\n" + "=" * 75)
    print("      GMAIL AI ASSISTANT - LANGCHAIN TRIAGE AGENT VERIFICATION")
    print("=" * 75 + "\n")


async def verify_db():
    print("[1/4] Verifying database connection & schema...")
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            from sqlalchemy import text
            await conn.execute(text("SELECT 1"))
        await init_db()
        print("  [OK] Database connected; schema tables ensured.")
        return True
    except Exception as e:
        print(f"  [FAIL] Database connection failed: {e}")
        return False


async def list_accounts_and_backlog():
    print("\n[2/4] Connected accounts & untriaged backlog...")
    session_maker = get_session_maker()
    async with session_maker() as db:
        accounts = (await db.execute(select(Account).order_by(Account.updated_at.desc()))).scalars().all()
        if not accounts:
            print("  [FAIL] No connected accounts. Run scripts/verify_oauth.py first.")
            return None
        for a in accounts:
            print(f"  - {a.email}  (history_id={a.history_id or 'none'})")

        total_messages = (await db.execute(select(func.count(Message.id)))).scalar() or 0
        analyzed = (await db.execute(select(func.count(EmailAnalysis.id)))).scalar() or 0
        print(f"\n  Messages in DB:  {total_messages}")
        print(f"  Triaged:         {analyzed}")
        print(f"  Untriaged:       {max(0, total_messages - analyzed)}")
    return accounts[0].email


async def triage_by_message(message_uuid_str: str, force: bool):
    import uuid
    from app.db.session import get_session_maker
    session_maker = get_session_maker()
    async with session_maker() as db:
        result = await db.execute(select(Message).where(Message.id == uuid.UUID(message_uuid_str)))
        message = result.scalar_one_or_none()
        if not message:
            print(f"  [FAIL] Message {message_uuid_str} not found.")
            return False
        print(f"  Triaging message {message.id} (thread {message.thread_id}) force={force}...")
        outcome = await TriageService.triage_message(db, message, force=force)
        print("\n  --- Triage Outcome ---")
        print(f"  status:           {outcome.status}")
        print(f"  category:         {outcome.category}")
        print(f"  urgency_score:    {outcome.urgency_score}")
        print(f"  confidence:       {outcome.confidence}")
        print(f"  action_items:     {outcome.action_items_count}")
        print(f"  skipped:          {outcome.skipped}")
        if outcome.error:
            print(f"  error:            {outcome.error} (retryable={outcome.retryable})")

        # Synthesise the thread.
        if outcome.thread_id:
            print("\n  Synthesizing thread briefing...")
            summary = await TriageService.triage_thread(uuid.UUID(outcome.thread_id), force=force)
            print(f"  thread status:    {summary.status}")
            print(f"  messages_triaged: {summary.messages_triaged}")
            print(f"  messages_skipped: {summary.messages_skipped}")
            print(f"  synthesis:        {summary.synthesis}")
            if summary.error:
                print(f"  thread error:     {summary.error}")

        # Print the stored briefing + action items.
        async with session_maker() as db2:
            t = (await db2.execute(select(Thread).where(Thread.id == message.thread_id))).scalar_one_or_none()
            if t and t.summary:
                print("\n  --- Thread Briefing ---")
                print("  " + t.summary.replace("\n", "\n  "))
            items = (
                await db2.execute(
                    select(ActionItem).join(EmailAnalysis, EmailAnalysis.id == ActionItem.analysis_id).where(
                        EmailAnalysis.message_id == message.id
                    )
                )
            ).scalars().all()
            if items:
                print("\n  --- Action Items ---")
                for ai in items:
                    dl = ai.deadline.isoformat() if ai.deadline else "-"
                    print(f"  - [{ai.status}] {ai.description} (deadline={dl}, assignee={ai.assignee or '-'})")
        return outcome.status in ("ok", "skipped")


async def triage_by_gmail_thread(gmail_thread_id: str, force: bool):
    import uuid
    from app.db.session import get_session_maker
    session_maker = get_session_maker()
    async with session_maker() as db:
        t = (await db.execute(select(Thread).where(Thread.gmail_thread_id == gmail_thread_id))).scalar_one_or_none()
        if not t:
            print(f"  [FAIL] Thread {gmail_thread_id} not found in DB. Sync first.")
            return False
        print(f"  Triaging thread {t.id} ({gmail_thread_id}) force={force}...")
        summary = await TriageService.triage_thread(t.id, force=force)
        print("\n  --- Triage Run Summary ---")
        print(f"  status:           {summary.status}")
        print(f"  gmail_thread_id:  {summary.gmail_thread_id}")
        print(f"  messages_triaged: {summary.messages_triaged}")
        print(f"  messages_skipped: {summary.messages_skipped}")
        print(f"  action_items:     {summary.action_items}")
        print(f"  synthesis:        {summary.synthesis}")
        if summary.error:
            print(f"  error:            {summary.error}")
        if summary.reason:
            print(f"  reason:           {summary.reason}")

        async with session_maker() as db2:
            t2 = (await db2.execute(select(Thread).where(Thread.id == t.id))).scalar_one_or_none()
            if t2 and t2.summary:
                print("\n  --- Thread Briefing ---")
                print("  " + t2.summary.replace("\n", "\n  "))
        return summary.status in ("ok", "skipped")


async def main():
    parser = argparse.ArgumentParser(description="Gmail LangChain Triage Agent Verifier")
    parser.add_argument("--message-id", type=str, default=None, help="Internal message UUID to triage")
    parser.add_argument("--thread-id", type=str, default=None, help="Gmail thread id to triage")
    parser.add_argument("--force", action="store_true", help="Re-run even when content_hash is unchanged")
    parser.add_argument("--email", type=str, default=None, help="Target account email")
    args = parser.parse_args()

    banner()
    if not await verify_db():
        sys.exit(1)

    account_email = await list_accounts_and_backlog()
    if account_email is None:
        sys.exit(1)

    print("\n[3/4] Running triage...")
    ok = True
    if args.message_id:
        ok = await triage_by_message(args.message_id, args.force)
    elif args.thread_id:
        ok = await triage_by_gmail_thread(args.thread_id, args.force)
    else:
        print("  [FAIL] Provide --message-id or --thread-id.")
        sys.exit(2)

    print("\n[4/4] " + ("Verification PASSED." if ok else "Verification FAILED."))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
