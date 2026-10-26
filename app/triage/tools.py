"""Per-run LangChain tool factory for the email triage agent.

All four tools (``inspect_email_payload``, ``classify_urgency_and_category``,
``extract_action_items_and_deadlines``, ``synthesize_thread_summary``) are
constructed per run and close over the run's ``AsyncSession`` and a small
``TriageRunState``. They read PostgreSQL only and never call Gmail. Writes for
a single message flush into the open transaction; the worker commits once at
the end (decision D6), so a model failure rolls back with zero partial rows.

READ-ONLY GUARD INVENTORY (audit this list before any release):
- No tool imports or instantiates ``GmailClient``.
- No tool calls ``users.messages.send``, ``users.drafts.create``,
  ``users.messages.delete``, ``users.messages.modify``, or ``users.settings``.
- No Gmail write scope is ever requested. The service layer requests only
  ``gmail.readonly`` + ``userinfo.email`` (see ``app/services/auth_service.py``).
- These tools read only from the local PostgreSQL tables (messages, threads,
  email_analyses, action_items).
"""
import logging
import uuid
from dataclasses import dataclass, field
from datetime import timezone
from typing import Any, Dict, List, Optional

from langchain_core.tools import BaseTool, ToolException, tool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.analysis import ActionItem, EmailAnalysis
from app.models.message import Message
from app.models.thread import Thread
from app.triage.schemas import (
    ActionItemResult,
    CategoryEnum,
    ClassificationResult,
    content_hash,
    normalise_to_utc,
)

logger = logging.getLogger("gmail_assistant.triage.tools")


@dataclass
class TriageRunState:
    """Small per-run handle closed over by the tools."""

    message_id: Optional[str] = None
    thread_id: Optional[str] = None
    content_hash: Optional[str] = None
    last_classification: Optional[ClassificationResult] = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_uuid(value: str) -> uuid.UUID:
    """Parse a string UUID, raising a ToolException on bad input."""
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as e:
        raise ToolException(f"Invalid id (expected a UUID): {value!r}") from e


async def _load_message(db: AsyncSession, message_id: str) -> Message:
    msg_uuid = _parse_uuid(message_id)
    result = await db.execute(select(Message).where(Message.id == msg_uuid))
    message = result.scalar_one_or_none()
    if message is None:
        raise ToolException(f"Unknown message_id: {message_id}")
    return message


async def _resolve_thread(db: AsyncSession, thread_id: str) -> Thread:
    """Resolve a thread by internal UUID or Gmail thread id."""
    # Try internal UUID first
    try:
        tid = uuid.UUID(str(thread_id))
        result = await db.execute(select(Thread).where(Thread.id == tid))
        thread = result.scalar_one_or_none()
        if thread is not None:
            return thread
    except (ValueError, AttributeError, TypeError):
        pass

    # Fall back to Gmail thread id
    result = await db.execute(select(Thread).where(Thread.gmail_thread_id == str(thread_id)))
    thread = result.scalar_one_or_none()
    if thread is None:
        raise ToolException(f"Unknown thread_id: {thread_id}")
    return thread


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_triage_tools(db: AsyncSession, state: TriageRunState) -> List[BaseTool]:
    """Build the four triage tools bound to ``db`` and ``state``.

    Tools close over the session; no module-level session, no implicit commits
    inside individual tools. The caller (worker) commits once per message.
    """

    @tool
    async def inspect_email_payload(message_id: str) -> Dict[str, Any]:
        """Load a stored message, its thread context, and prior messages from PostgreSQL.

        Inputs:
            message_id: Internal message UUID (string form).

        Side effects:
            None. This tool is read-only; it queries the local database only and
            never calls the Gmail API.

        Returns:
            A compact payload: subject, sender, recipient, date (ISO), body_text
            truncated to TRIAGE_MAX_BODY_CHARS, attachment metadata, and up to
            three prior message summaries in the same thread for context.
        """
        settings = get_settings()
        message = await _load_message(db, message_id)

        body = (message.body_text or message.snippet or "")
        max_chars = settings.TRIAGE_MAX_BODY_CHARS
        if len(body) > max_chars:
            body = body[:max_chars] + "…"

        # Prior messages in the thread (chronological, excluding this one)
        prior_stmt = (
            select(Message)
            .where(Message.thread_id == message.thread_id, Message.id != message.id)
            .order_by(Message.date.asc())
            .limit(3)
        )
        prior_result = await db.execute(prior_stmt)
        prior_messages = prior_result.scalars().all()
        prior_context = [
            {
                "sender": p.sender,
                "date": p.date.isoformat() if p.date else None,
                "snippet": (p.snippet or "")[:280],
            }
            for p in prior_messages
        ]

        return {
            "message_id": str(message.id),
            "thread_id": str(message.thread_id),
            "subject": message.subject,
            "sender": message.sender,
            "recipient": message.recipient,
            "date": message.date.isoformat() if message.date else None,
            "body_text": body,
            "has_attachments": message.has_attachments,
            "attachment_names": list(message.attachment_names or []),
            "prior_context": prior_context,
        }

    @tool
    async def classify_urgency_and_category(
        message_id: str,
        category: str,
        urgency_score: int,
        rationale: str,
        confidence: float,
    ) -> str:
        """Classify a message's urgency and category and persist the analysis.

        Inputs:
            message_id: Internal message UUID (string form).
            category: One of URGENT, ACTION_REQUIRED, INVOICES_RECEIPTS,
                SCHEDULING, UPDATES_NEWSLETTERS, GENERAL.
            urgency_score: Integer 1-5 (1 = ignore, 5 = respond today).
            rationale: Concise reasoning / summary text.
            confidence: Model confidence 0.0-1.0.

        Side effects:
            Upserts exactly one ``email_analyses`` row keyed by ``message_id``
            (refreshes ``content_hash`` and ``updated_at``). Never calls Gmail.
            The row is flushed (not committed); the worker commits once.

        Returns:
            One-line confirmation string.
        """
        classification = ClassificationResult(
            message_id=message_id,
            category=category,
            urgency_score=urgency_score,
            rationale=rationale,
            confidence=confidence,
        )

        message = await _load_message(db, message_id)
        ch = content_hash(message.subject, message.sender, message.date, message.body_text)

        result = await db.execute(
            select(EmailAnalysis).where(EmailAnalysis.message_id == message.id)
        )
        analysis = result.scalar_one_or_none()
        if analysis is None:
            analysis = EmailAnalysis(
                id=uuid.uuid4(),
                message_id=message.id,
                category=classification.category.value,
                urgency_score=classification.urgency_score,
                summary=classification.rationale,
                confidence=classification.confidence,
                content_hash=ch,
            )
            db.add(analysis)
        else:
            analysis.category = classification.category.value
            analysis.urgency_score = classification.urgency_score
            analysis.summary = classification.rationale
            analysis.confidence = classification.confidence
            analysis.content_hash = ch

        await db.flush()
        state.last_classification = classification
        return (
            f"Classified message {message_id} as {classification.category.value} "
            f"(urgency {classification.urgency_score}, confidence {classification.confidence:.2f})."
        )

    @tool
    async def extract_action_items_and_deadlines(
        message_id: str,
        items: List[Dict[str, Any]],
    ) -> str:
        """Persist action items and deadlines for an already-classified message.

        Inputs:
            message_id: Internal message UUID (string form). The message must
                have been classified first (this tool enforces the ordering).
            items: List of {description, deadline?, assignee?} objects. Naive
                deadlines are normalised to UTC. Missing deadlines must be null
                (never invented).

        Side effects:
            Replaces the existing ``action_items`` for the message's analysis
            (delete-then-insert semantics). Never calls Gmail.

        Returns:
            Confirmation with the number of items persisted.

        Raises:
            ToolException: If the message has not been classified yet.
        """
        message = await _load_message(db, message_id)

        result = await db.execute(
            select(EmailAnalysis).where(EmailAnalysis.message_id == message.id)
        )
        analysis = result.scalar_one_or_none()
        if analysis is None:
            raise ToolException(
                f"Cannot extract action items for {message_id}: classify_urgency_and_category first."
            )

        # Validate through Pydantic (enforces min length, deadlines, assignee).
        validated: List[ActionItemResult] = []
        for raw in items or []:
            validated.append(ActionItemResult.model_validate(raw))

        # Replace existing items.
        existing = await db.execute(
            select(ActionItem).where(ActionItem.analysis_id == analysis.id)
        )
        for old in existing.scalars().all():
            await db.delete(old)
        await db.flush()

        if message.date and message.date.tzinfo is None:
            message_date = message.date.replace(tzinfo=timezone.utc)
        else:
            message_date = message.date

        for item in validated:
            deadline = normalise_to_utc(item.deadline)
            if deadline and message_date and deadline < message_date:
                logger.warning(
                    "Action item deadline %s precedes message date %s for message %s; keeping as-is.",
                    deadline,
                    message.date,
                    message_id,
                )
            db.add(
                ActionItem(
                    id=uuid.uuid4(),
                    analysis_id=analysis.id,
                    message_id=message.id,
                    description=item.description,
                    deadline=deadline,
                    assignee=item.assignee,
                    status="pending",
                )
            )

        await db.flush()
        return f"Recorded {len(validated)} action item(s) for message {message_id}."

    @tool
    async def synthesize_thread_summary(
        thread_id: str,
        summary: str,
        decisions: Optional[List[str]] = None,
        pending_items: Optional[List[str]] = None,
    ) -> str:
        """Write an executive briefing to ``threads.summary``.

        Inputs:
            thread_id: Internal thread UUID or Gmail thread id.
            summary: Concise executive summary (non-empty).
            decisions: Optional list of decisions made in the thread.
            pending_items: Optional list of pending/outstanding items.

        Side effects:
            Composes markdown (## Summary / ## Decisions / ## Pending) and
            stores it on the ``threads`` row, capped at 8000 characters. Never
            calls Gmail.

        Returns:
            One-line confirmation.
        """
        if not summary or not summary.strip():
            raise ToolException("summary must be a non-empty string")

        thread = await _resolve_thread(db, thread_id)
        decisions = decisions or []
        pending_items = pending_items or []

        sections: List[str] = [f"## Summary\n\n{summary.strip()}"]
        if decisions:
            sections.append("## Decisions\n\n" + "\n".join(f"- {d}" for d in decisions))
        if pending_items:
            sections.append("## Pending\n\n" + "\n".join(f"- {p}" for p in pending_items))
        markdown = "\n\n".join(sections)

        if len(markdown) > 8000:
            markdown = markdown[:7999] + "…"

        thread.summary = markdown
        await db.flush()
        return f"Stored thread summary for thread {thread_id} ({len(markdown)} chars)."

    return [inspect_email_payload, classify_urgency_and_category, extract_action_items_and_deadlines, synthesize_thread_summary]
