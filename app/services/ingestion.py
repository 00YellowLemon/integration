"""Gmail email ingestion and delta synchronization service with strict idempotency."""
from dataclasses import dataclass
import logging
from typing import Any, Dict, Optional, Tuple
import uuid
from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.message import Message
from app.models.thread import Thread
from app.services.auth_service import AuthService
from app.services.gmail import GmailClient
from app.services.mime_parser import MIMEParser

logger = logging.getLogger("gmail_assistant.ingestion")


@dataclass
class SyncStats:
    """Summary of completed mailbox sync operation."""

    account_email: str
    threads_synced: int
    messages_synced: int
    new_history_id: str
    # Internal thread UUIDs touched by this sync (Phase 3 triage scheduling).
    thread_ids: list = None

    def __post_init__(self):
        if self.thread_ids is None:
            self.thread_ids = []


class IngestionService:
    """Service orchestrating initial backfill, delta synchronization, and thread upserts."""

    @staticmethod
    async def ingest_thread(
        db: AsyncSession,
        account_id: uuid.UUID,
        gmail_thread_resource: Dict[str, Any],
    ) -> Tuple[Thread, int]:
        """
        Idempotently upsert a Gmail conversation thread and all contained messages.
        Returns: (Thread, number_of_messages_processed)
        """
        gmail_thread_id = gmail_thread_resource.get("id")
        if not gmail_thread_id:
            raise ValueError("Thread resource missing 'id'")

        # 1. Fetch or instantiate Thread
        stmt = select(Thread).where(Thread.gmail_thread_id == gmail_thread_id)
        result = await db.execute(stmt)
        thread = result.scalar_one_or_none()

        if not thread:
            thread = Thread(
                id=uuid.uuid4(),
                account_id=account_id,
                gmail_thread_id=gmail_thread_id,
                snippet=gmail_thread_resource.get("snippet"),
                history_id=str(gmail_thread_resource.get("historyId", "")),
            )
            db.add(thread)
            await db.flush()
        else:
            if gmail_thread_resource.get("snippet"):
                thread.snippet = gmail_thread_resource.get("snippet")
            if gmail_thread_resource.get("historyId"):
                thread.history_id = str(gmail_thread_resource.get("historyId"))

        # 2. Iterate through messages and upsert idempotently
        raw_messages = gmail_thread_resource.get("messages", [])
        messages_processed = 0
        latest_date = thread.last_message_at

        for raw_msg in raw_messages:
            parsed = MIMEParser.parse_message(raw_msg)
            if not parsed.gmail_message_id:
                continue

            msg_stmt = select(Message).where(Message.gmail_message_id == parsed.gmail_message_id)
            msg_res = await db.execute(msg_stmt)
            message = msg_res.scalar_one_or_none()

            if not message:
                message = Message(
                    id=uuid.uuid4(),
                    gmail_message_id=parsed.gmail_message_id,
                    thread_id=thread.id,
                    sender=parsed.sender,
                    recipient=parsed.recipient,
                    subject=parsed.subject,
                    date=parsed.date,
                    snippet=parsed.snippet,
                    body_text=parsed.body_text,
                    body_html=parsed.body_html,
                    has_attachments=parsed.has_attachments,
                    attachment_names=parsed.attachment_names,
                    raw_headers=parsed.raw_headers,
                )
                db.add(message)
            else:
                message.sender = parsed.sender
                message.recipient = parsed.recipient
                message.subject = parsed.subject
                message.date = parsed.date
                message.snippet = parsed.snippet
                message.body_text = parsed.body_text
                message.body_html = parsed.body_html
                message.has_attachments = parsed.has_attachments
                message.attachment_names = parsed.attachment_names
                message.raw_headers = parsed.raw_headers

            messages_processed += 1

            if latest_date is None or parsed.date > latest_date:
                latest_date = parsed.date

            if not thread.subject and parsed.subject:
                thread.subject = parsed.subject

        thread.last_message_at = latest_date
        await db.flush()
        return thread, messages_processed

    @classmethod
    async def initial_backfill(
        cls,
        db: AsyncSession,
        account: Account,
        max_threads: int = 50,
    ) -> SyncStats:
        """
        Execute initial backfill on account by fetching recent threads and storing them.
        """
        credentials = await AuthService.get_valid_credentials(db, email=account.email)
        client = GmailClient(credentials=credentials)

        threads_resp = await client.list_threads(max_results=max_threads)
        thread_summaries = threads_resp.get("threads", [])

        threads_count = 0
        messages_count = 0
        affected_thread_ids: list = []

        for item in thread_summaries:
            tid = item.get("id")
            if not tid:
                continue
            try:
                full_thread = await client.get_thread(tid)
                thread, msgs = await cls.ingest_thread(db, account.id, full_thread)
                threads_count += 1
                messages_count += msgs
                affected_thread_ids.append(thread.id)
            except Exception as e:
                logger.warning("Failed to ingest thread %s during backfill: %s", tid, e)

        # Retrieve latest historyId from Gmail profile checkpoint
        new_history_id = account.history_id or ""
        try:
            profile = await client.get_profile()
            if profile.get("historyId"):
                new_history_id = str(profile["historyId"])
        except Exception as e:
            logger.warning("Failed to fetch profile historyId after backfill: %s", e)

        account.history_id = new_history_id
        await db.commit()

        return SyncStats(
            account_email=account.email,
            threads_synced=threads_count,
            messages_synced=messages_count,
            new_history_id=new_history_id,
            thread_ids=affected_thread_ids,
        )

    @classmethod
    async def delta_sync(
        cls,
        db: AsyncSession,
        account: Account,
    ) -> SyncStats:
        """
        Execute incremental delta sync using users.history.list.
        Falls back to initial backfill if historyId is invalid or expired.
        """
        if not account.history_id:
            logger.info("Account %s has no historyId; running initial backfill", account.email)
            return await cls.initial_backfill(db, account)

        credentials = await AuthService.get_valid_credentials(db, email=account.email)
        client = GmailClient(credentials=credentials)

        try:
            history_resp = await client.list_history(start_history_id=account.history_id)
        except HttpError as e:
            if e.resp.status == 404 or "history" in str(e).lower():
                logger.warning("History ID %s expired (HTTP %s). Triggering backfill.", account.history_id, e.resp.status)
                return await cls.initial_backfill(db, account)
            raise
        except Exception as e:
            if "404" in str(e) or "history" in str(e).lower():
                logger.warning("History ID %s expired or missing (%s). Triggering backfill.", account.history_id, e)
                return await cls.initial_backfill(db, account)
            raise

        history_records = history_resp.get("history", [])
        new_history_id = str(history_resp.get("historyId") or account.history_id)

        if not history_records:
            account.history_id = new_history_id
            await db.commit()
            return SyncStats(
                account_email=account.email,
                threads_synced=0,
                messages_synced=0,
                new_history_id=new_history_id,
                thread_ids=[],
            )

        # Collect unique Gmail thread IDs affected by history changes
        affected_gmail_thread_ids = set()
        for record in history_records:
            for item in record.get("messagesAdded", []):
                msg = item.get("message", {})
                if msg.get("threadId"):
                    affected_gmail_thread_ids.add(msg["threadId"])
            for msg in record.get("messages", []):
                if msg.get("threadId"):
                    affected_gmail_thread_ids.add(msg["threadId"])
            for item in record.get("labelsAdded", []):
                msg = item.get("message", {})
                if msg.get("threadId"):
                    affected_gmail_thread_ids.add(msg["threadId"])
            for item in record.get("labelsRemoved", []):
                msg = item.get("message", {})
                if msg.get("threadId"):
                    affected_gmail_thread_ids.add(msg["threadId"])

        threads_synced = 0
        messages_synced = 0
        affected_thread_ids: list = []

        for tid in affected_gmail_thread_ids:
            try:
                full_thread = await client.get_thread(tid)
                thread, msgs = await cls.ingest_thread(db, account.id, full_thread)
                threads_synced += 1
                messages_synced += msgs
                affected_thread_ids.append(thread.id)
            except Exception as e:
                logger.warning("Failed to sync delta thread %s: %s", tid, e)

        account.history_id = new_history_id
        await db.commit()

        return SyncStats(
            account_email=account.email,
            threads_synced=threads_synced,
            messages_synced=messages_synced,
            new_history_id=new_history_id,
            thread_ids=affected_thread_ids,
        )
