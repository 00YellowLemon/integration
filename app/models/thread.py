"""SQLAlchemy Thread model for Gmail conversation threads."""
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional
import uuid
from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.account import GUID

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.message import Message


class Thread(Base):
    """Thread model representing a Gmail conversation thread."""

    __tablename__ = "threads"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        primary_key=True,
        default=uuid.uuid4,
    )
    gmail_thread_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    subject: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
    )
    snippet: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    history_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
    )
    last_message_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    summary: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    account: Mapped["Account"] = relationship(
        "Account",
        back_populates="threads",
    )
    messages: Mapped[List["Message"]] = relationship(
        "Message",
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="Message.date.asc()",
    )

    def __repr__(self) -> str:
        return f"<Thread id={self.id} gmail_thread_id={self.gmail_thread_id} subject={self.subject}>"
