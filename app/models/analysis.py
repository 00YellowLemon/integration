"""SQLAlchemy models for triage analysis output and extracted action items.

These tables are populated by the LangChain Email Triage Agent (Phase 3):

- ``email_analyses``: one current analysis per message (category, urgency,
  summary, confidence, content_hash). Unique on ``message_id`` so re-runs
  upsert in place instead of duplicating rows.
- ``action_items``: discrete tasks extracted from a message, owned by an
  analysis. Replace-semantics: a re-run deletes and re-inserts the list.

Both foreign keys cascade on delete to keep the data graph consistent when a
message or analysis is removed.
"""
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.account import GUID
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """DateTime type that preserves UTC timezone awareness across PostgreSQL and SQLite."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

if TYPE_CHECKING:
    from app.models.message import Message


class EmailAnalysis(Base):
    """Structured triage output for a single ingested message.

    One row per message (enforced by the unique ``message_id`` constraint).
    Re-running triage upserts this row and refreshes ``content_hash`` /
    ``updated_at``; the ``content_hash`` enables idempotent skip logic so
    repeated Pub/Sub deliveries do not double-bill Gemini.
    """

    __tablename__ = "email_analyses"
    __table_args__ = (
        CheckConstraint("urgency_score BETWEEN 1 AND 5", name="ck_email_analyses_urgency"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_email_analyses_confidence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("messages.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    urgency_score: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    message: Mapped["Message"] = relationship(
        "Message",
        back_populates="analysis",
    )
    action_items: Mapped[List["ActionItem"]] = relationship(
        "ActionItem",
        back_populates="analysis",
        cascade="all, delete-orphan",
        order_by="ActionItem.created_at.asc()",
    )

    def __repr__(self) -> str:
        return (
            f"<EmailAnalysis id={self.id} message_id={self.message_id} "
            f"category={self.category} urgency={self.urgency_score}>"
        )


class ActionItem(Base):
    """A discrete actionable task extracted from a triaged message."""

    __tablename__ = "action_items"
    __table_args__ = (
        Index("ix_action_items_deadline_status", "deadline", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("email_analyses.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("messages.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    deadline: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime,
        index=True,
        nullable=True,
    )
    assignee: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        index=True,
        default="pending",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    analysis: Mapped["EmailAnalysis"] = relationship(
        "EmailAnalysis",
        back_populates="action_items",
    )
    message: Mapped["Message"] = relationship(
        "Message",
        back_populates="action_items",
    )

    def __repr__(self) -> str:
        return f"<ActionItem id={self.id} status={self.status} deadline={self.deadline}>"
