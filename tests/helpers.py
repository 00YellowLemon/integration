"""Shared helpers for the Phase 3 triage test suite.

Seeds accounts, threads, and messages into the in-memory SQLite test DB using
the same patterns as the Phase 1/2 fixtures.
"""
import base64
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.message import Message
from app.models.thread import Thread
from app.services.token_store import TokenStore


async def seed_account(
    db: AsyncSession,
    email: str = "triage@example.com",
    history_id: Optional[str] = "1000",
) -> Account:
    """Insert an active account row."""
    account = await TokenStore.save_tokens(
        db=db,
        email=email,
        access_token="mock-access-token",
        refresh_token="mock-refresh-token",
        expiry=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    if history_id is not None:
        account.history_id = history_id
    await db.commit()
    await db.refresh(account)
    return account


async def seed_thread(
    db: AsyncSession,
    account: Account,
    *,
    gmail_thread_id: str = "th_triage_1",
    subject: str = "Q4 Planning",
    summary: Optional[str] = None,
) -> Thread:
    thread = Thread(
        id=uuid.uuid4(),
        account_id=account.id,
        gmail_thread_id=gmail_thread_id,
        subject=subject,
        snippet="snippet",
        history_id="1001",
        summary=summary,
    )
    db.add(thread)
    await db.flush()
    return thread


async def seed_message(
    db: AsyncSession,
    thread: Thread,
    *,
    body_text: str = "Please review the Q4 roadmap and confirm the deadline by Friday.",
    subject: str = "Q4 Planning",
    sender: str = "boss@example.com",
    gmail_message_id: str = "msg_triage_1",
    date: Optional[datetime] = None,
) -> Message:
    message = Message(
        id=uuid.uuid4(),
        gmail_message_id=gmail_message_id,
        thread_id=thread.id,
        sender=sender,
        recipient="me@example.com",
        subject=subject,
        date=date or datetime.now(timezone.utc),
        snippet=body_text[:80],
        body_text=body_text,
        has_attachments=False,
        attachment_names=[],
        raw_headers={},
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return message


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("utf-8").rstrip("=")


def make_gmail_thread_resource(thread_id: str, message_ids: List[str]) -> dict:
    """Build a mock Gmail thread resource for ingestion tests."""
    messages = []
    for idx, mid in enumerate(message_ids):
        messages.append({
            "id": mid,
            "threadId": thread_id,
            "snippet": f"Snippet for {mid}",
            "internalDate": str(1729680000000 + idx * 1000),
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": f"sender{idx}@example.com"},
                    {"name": "To", "value": "user@example.com"},
                    {"name": "Subject", "value": "Test Subject"},
                    {"name": "Date", "value": "Fri, 23 Oct 2026 10:00:00 +0000"},
                ],
                "body": {"data": b64(f"Message body content for {mid}"), "size": 30},
            },
        })
    return {
        "id": thread_id,
        "historyId": "1001",
        "messages": messages,
        "snippet": f"Snippet for {message_ids[-1]}",
    }
