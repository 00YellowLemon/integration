"""SQLAlchemy Message model for individual email messages."""
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional
import uuid
from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.account import GUID

if TYPE_CHECKING:
    from app.models.thread import Thread
    from app.models.analysis import EmailAnalysis, ActionItem

JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


class Message(Base):
    """Message model representing an individual email message in a thread."""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        primary_key=True,
        default=uuid.uuid4,
    )
    gmail_message_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("threads.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    sender: Mapped[str] = mapped_column(
        String(255),
        index=True,
        nullable=False,
    )
    recipient: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    subject: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
    )
    date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
    )
    snippet: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    body_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    body_html: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    has_attachments: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    attachment_names: Mapped[List[Dict[str, Any]]] = mapped_column(
        JSON_TYPE,
        default=list,
        nullable=False,
    )
    raw_headers: Mapped[Dict[str, Any]] = mapped_column(
        JSON_TYPE,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    thread: Mapped["Thread"] = relationship(
        "Thread",
        back_populates="messages",
    )
    analysis: Mapped[Optional["EmailAnalysis"]] = relationship(
        "EmailAnalysis",
        back_populates="message",
        uselist=False,
    )
    action_items: Mapped[List["ActionItem"]] = relationship(
        "ActionItem",
        back_populates="message",
    )

    def __repr__(self) -> str:
        return f"<Message id={self.id} gmail_message_id={self.gmail_message_id} sender={self.sender} date={self.date}>"
