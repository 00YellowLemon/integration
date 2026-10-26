"""Post-ingestion triage worker: per-message triage and thread synthesis.

Responsibilities (decisions D2, D6, D8, D9):
- ``triage_message``: compute ``content_hash``, skip unchanged analyses, run
  the agent chain (inspect -> classify -> extract) under the thread-scoped
  memory, commit once, and translate failures into a retryable outcome.
- ``triage_thread``: open an isolated session, acquire a per-thread advisory
  lock (Postgres), iterate messages chronologically, then run one synthesis
  pass when anything changed. Skip cleanly when the lock is contended.
- ``triage_after_sync``: background entry point with per-thread try/except
  and identifier-only structured logging.

All logging uses identifiers only — never email bodies or prompt text.
``initial_backfill`` does NOT enqueue triage (D9); only delta-sync additions
and explicit calls are analyzed.
"""
import logging
import struct
import uuid
from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_maker
from app.models.analysis import ActionItem, EmailAnalysis
from app.models.message import Message
from app.models.thread import Thread
from app.triage.agent import TriageDisabledError, build_agent, invoke_triage
from app.triage.threads import build_thread_digest
from app.triage.tools import TriageRunState
from app.triage.schemas import content_hash

logger = logging.getLogger("gmail_assistant.triage.service")


@dataclass
class TriageOutcome:
    """Outcome of a single-message triage run."""

    status: str  # ok | skipped | error
    message_id: str
    thread_id: Optional[str] = None
    category: Optional[str] = None
    urgency_score: Optional[int] = None
    confidence: Optional[float] = None
    action_items_count: int = 0
    skipped: bool = False
    content_hash: Optional[str] = None
    error: Optional[str] = None
    retryable: bool = False


@dataclass
class TriageRunSummaryResult:
    """Outcome of a thread-scoped triage run."""

    thread_id: Optional[str]
    gmail_thread_id: Optional[str]
    status: str  # ok | skipped | error
    messages_triaged: int = 0
    messages_skipped: int = 0
    action_items: int = 0
    synthesis: bool = False
    error: Optional[str] = None
    reason: Optional[str] = None


def _thread_lock_key(thread_id: uuid.UUID) -> int:
    """Map a thread UUID to a 64-bit int for ``pg_try_advisory_lock``."""
    raw = thread_id.bytes
    return struct.unpack(">q", raw[:8])[0]


def _is_postgres(db: AsyncSession) -> bool:
    bind = db.get_bind()
    try:
        name = bind.dialect.name
    except Exception:
        return False
    return name == "postgresql"


class TriageService:
    """Orchestrates the LangChain triage agent over stored messages."""

    @staticmethod
    def _build_instruction_for_message(message: Message) -> str:
        return (
            f"Triage message {message.id}. Call inspect_email_payload(\"{message.id}\"), "
            f"then classify_urgency_and_category, then extract_action_items_and_deadlines. "
            f"Use the message UUID exactly as given."
        )

    @staticmethod
    def _build_synthesis_instruction(thread: Thread) -> str:
        tid = str(thread.id)
        return (
            f"Synthesize an executive briefing for thread {tid}. "
            f"Call synthesize_thread_summary(thread_id=\"{tid}\", summary, decisions, pending_items) "
            f"based on the prior messages you have already triaged."
        )

    @classmethod
    async def triage_message(
        cls,
        db: AsyncSession,
        message: Message,
        *,
        force: bool = False,
        model=None,
        checkpointer=None,
    ) -> TriageOutcome:
        """Triage a single message, committing once (or rolling back on failure)."""
        ch = content_hash(message.subject, message.sender, message.date, message.body_text)

        existing = await db.execute(
            select(EmailAnalysis).where(EmailAnalysis.message_id == message.id)
        )
        analysis = existing.scalar_one_or_none()
        if analysis is not None and not force and analysis.content_hash == ch:
            items = await db.execute(
                select(func.count(ActionItem.id)).where(ActionItem.analysis_id == analysis.id)
            )
            logger.info(
                "triage.skip message_id=%s (unchanged content_hash)", message.id
            )
            return TriageOutcome(
                status="skipped",
                message_id=str(message.id),
                thread_id=str(message.thread_id),
                category=analysis.category,
                urgency_score=analysis.urgency_score,
                confidence=analysis.confidence,
                action_items_count=int(items.scalar() or 0),
                skipped=True,
                content_hash=ch,
            )

        state = TriageRunState(
            message_id=str(message.id),
            thread_id=str(message.thread_id),
            content_hash=ch,
        )

        logger.info("triage.start message_id=%s thread_id=%s", message.id, message.thread_id)
        msg_id_str = str(message.id)
        thread_id_str = str(message.thread_id)
        try:
            agent = build_agent(db, state, model=model, checkpointer=checkpointer)
            await invoke_triage(
                agent,
                msg_id_str,
                thread_id_str,
                cls._build_instruction_for_message(message),
            )
        except TriageDisabledError as e:
            logger.warning("triage.disabled message_id=%s: %s", msg_id_str, e)
            return TriageOutcome(
                status="error",
                message_id=msg_id_str,
                thread_id=thread_id_str,
                error=str(e),
                retryable=False,
                content_hash=ch,
            )
        except Exception as e:
            logger.error("triage.error message_id=%s: %s", msg_id_str, e)
            await db.rollback()
            return TriageOutcome(
                status="error",
                message_id=msg_id_str,
                thread_id=thread_id_str,
                error=str(e),
                retryable=True,
                content_hash=ch,
            )

        await db.commit()

        refreshed = await db.execute(
            select(EmailAnalysis).where(EmailAnalysis.message_id == message.id)
        )
        new_analysis = refreshed.scalar_one_or_none()
        items_count = 0
        if new_analysis is not None:
            items = await db.execute(
                select(func.count(ActionItem.id)).where(ActionItem.analysis_id == new_analysis.id)
            )
            items_count = int(items.scalar() or 0)

        logger.info(
            "triage.message.done message_id=%s category=%s urgency=%s",
            msg_id_str,
            new_analysis.category if new_analysis else None,
            new_analysis.urgency_score if new_analysis else None,
        )
        return TriageOutcome(
            status="ok",
            message_id=msg_id_str,
            thread_id=thread_id_str,
            category=new_analysis.category if new_analysis else None,
            urgency_score=new_analysis.urgency_score if new_analysis else None,
            confidence=new_analysis.confidence if new_analysis else None,
            action_items_count=items_count,
            skipped=False,
            content_hash=ch,
        )

    @classmethod
    async def _synthesize_thread(
        cls,
        db: AsyncSession,
        thread: Thread,
        messages: List[Message],
        *,
        model=None,
        checkpointer=None,
    ) -> bool:
        """Run one synthesis pass and write ``threads.summary``. Returns True on success."""
        state = TriageRunState(
            message_id=None,
            thread_id=str(thread.id),
            content_hash=None,
        )
        digest = build_thread_digest(thread, messages)
        instruction = (
            cls._build_synthesis_instruction(thread)
            + "\n\nThread digest:\n" + digest
        )
        logger.info("triage.synthesis.start thread_id=%s", thread.id)
        try:
            agent = build_agent(db, state, model=model, checkpointer=checkpointer)
            await invoke_triage(
                agent,
                str(thread.id),
                str(thread.id),
                instruction,
            )
        except TriageDisabledError as e:
            logger.warning("triage.synthesis.disabled thread_id=%s: %s", thread.id, e)
            return False
        except Exception as e:
            logger.error("triage.synthesis.error thread_id=%s: %s", thread.id, e)
            await db.rollback()
            return False

        await db.commit()
        logger.info("triage.thread.done thread_id=%s messages=%s", thread.id, len(messages))
        return True

    @classmethod
    async def triage_thread(
        cls,
        thread_id: uuid.UUID,
        *,
        force: bool = False,
        model=None,
        checkpointer=None,
        session_maker=None,
    ) -> TriageRunSummaryResult:
        """Triage all messages in a thread, then run one synthesis pass.

        Opens an isolated session, acquires a per-thread advisory lock on
        Postgres (skips cleanly when contended), and runs per-message triage
        followed by synthesis when anything changed.
        """
        maker = session_maker or get_session_maker()
        async with maker() as db:
            thread = await db.execute(select(Thread).where(Thread.id == thread_id))
            thread_row = thread.scalar_one_or_none()
            if thread_row is None:
                return TriageRunSummaryResult(
                    thread_id=str(thread_id),
                    gmail_thread_id=None,
                    status="error",
                    error="thread not found",
                )

            lock_key = _thread_lock_key(thread_id)
            locked = True
            if _is_postgres(db):
                lock_res = await db.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_key}
                )
                locked = bool(lock_res.scalar())

            if not locked:
                logger.info("triage.thread.locked thread_id=%s (skipped)", thread_id)
                return TriageRunSummaryResult(
                    thread_id=str(thread_id),
                    gmail_thread_id=thread_row.gmail_thread_id,
                    status="skipped",
                    reason="lock contended",
                )

            try:
                msgs_res = await db.execute(
                    select(Message)
                    .where(Message.thread_id == thread_id)
                    .order_by(Message.date.asc())
                )
                messages = list(msgs_res.scalars().all())

                settings_budget = cls._max_messages_per_thread()
                messages_to_triage = messages[:settings_budget]

                triaged = 0
                skipped = 0
                changed = False
                for message in messages_to_triage:
                    outcome = await cls.triage_message(
                        db, message, force=force, model=model, checkpointer=checkpointer
                    )
                    if outcome.skipped:
                        skipped += 1
                    elif outcome.status == "ok":
                        triaged += 1
                        changed = True
                    elif outcome.status == "error" and outcome.retryable:
                        # Keep going for other messages; this one is left unanalyzed (D8).
                        continue

                synthesis_ran = False
                # Synthesize when something changed, a forced re-run, or no summary exists yet.
                needs_synthesis = bool(changed or force or thread_row.summary is None)
                if needs_synthesis and messages:
                    synthesis_ran = await cls._synthesize_thread(
                        db, thread_row, messages, model=model, checkpointer=checkpointer
                    )

                total_items = await db.execute(
                    select(func.count(ActionItem.id)).join(
                        EmailAnalysis, EmailAnalysis.id == ActionItem.analysis_id
                    ).join(Message, Message.id == EmailAnalysis.message_id).where(
                        Message.thread_id == thread_id
                    )
                )
                action_items_total = int(total_items.scalar() or 0)

                return TriageRunSummaryResult(
                    thread_id=str(thread_id),
                    gmail_thread_id=thread_row.gmail_thread_id,
                    status="ok",
                    messages_triaged=triaged,
                    messages_skipped=skipped,
                    action_items=action_items_total,
                    synthesis=synthesis_ran,
                )
            finally:
                if _is_postgres(db) and locked:
                    try:
                        await db.execute(
                            text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key}
                        )
                    except Exception as e:
                        logger.warning("Failed to release advisory lock: %s", e)

    @staticmethod
    def _max_messages_per_thread() -> int:
        from app.core.config import get_settings
        return get_settings().TRIAGE_MAX_MESSAGES_PER_THREAD

    @classmethod
    async def triage_after_sync(
        cls,
        thread_ids: List[uuid.UUID],
        *,
        model=None,
        checkpointer=None,
    ) -> None:
        """Background entry point. Triages each affected thread with per-thread isolation."""
        from app.core.config import get_settings

        if not thread_ids:
            return
        if not get_settings().TRIAGE_ENABLED:
            logger.info("triage.after_sync skipped (TRIAGE_ENABLED=false) threads=%s", len(thread_ids))
            return

        for tid in thread_ids:
            try:
                summary = await cls.triage_thread(
                    tid, model=model, checkpointer=checkpointer
                )
                logger.info(
                    "triage.after_sync.done thread_id=%s status=%s triaged=%s skipped=%s",
                    tid,
                    summary.status,
                    summary.messages_triaged,
                    summary.messages_skipped,
                )
            except Exception as e:
                logger.error("triage.after_sync.error thread_id=%s: %s", tid, e)
