"""Chronological thread digest builder and synthesis change-detection.

Used by the triage worker to (a) surface compact prior-message context to the
agent and (b) decide whether a thread needs a fresh synthesis pass.
"""
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import get_settings
from app.models.analysis import EmailAnalysis
from app.models.message import Message
from app.models.thread import Thread
from app.triage.schemas import content_hash


def build_thread_digest(thread: Thread, messages: List[Message]) -> str:
    """Compose an ordered transcript of a thread's messages.

    Each entry shows sender, date, subject, and a body truncated per the
    global ``TRIAGE_MAX_BODY_CHARS`` guardrail. The whole digest is capped at
    the same budget so synthesis never exceeds the configured prompt size.
    """
    settings = get_settings()
    budget = settings.TRIAGE_MAX_BODY_CHARS
    lines: List[str] = []
    remaining = budget

    for msg in messages:
        if remaining <= 0:
            lines.append("[...remaining messages truncated by triage budget...]")
            break

        date_str = msg.date.isoformat() if msg.date else "unknown"
        subject = msg.subject or "(no subject)"
        body = (msg.body_text or msg.snippet or "").strip()
        entry_header = f"### {msg.sender} — {date_str}\nSubject: {subject}"

        # Reserve room for the header + separators; cap body to the remaining budget.
        header_len = len(entry_header) + 2
        body_budget = max(0, remaining - header_len - 8)
        if len(body) > body_budget:
            body = body[:body_budget] + "…"
        entry = f"{entry_header}\n\n{body}"
        lines.append(entry)
        remaining -= len(entry) + 2

    header = f"# Thread: {thread.subject or '(no subject)'}\n"
    digest = header + "\n\n---\n\n".join(lines)
    if len(digest) > budget:
        digest = digest[:budget] + "…"
    return digest


async def changed_since_last_analysis(
    db: AsyncSession,
    thread: Thread,
    messages: List[Message],
) -> bool:
    """Return True if any message lacks an analysis or its content_hash changed.

    Used to skip redundant synthesis passes when nothing has been re-triaged.
    """
    if not messages:
        return False

    for msg in messages:
        current_hash = content_hash(msg.subject, msg.sender, msg.date, msg.body_text)
        stmt = select(EmailAnalysis).where(EmailAnalysis.message_id == msg.id)
        result = await db.execute(stmt)
        analysis = result.scalar_one_or_none()
        if analysis is None:
            return True
        if analysis.content_hash != current_hash:
            return True

    return False
