"""Pydantic triage schemas and the shared content-hash helper.

These models are the validated contracts for triage tool inputs (used by the
LangChain agent's ``@tool`` functions) and for the worker / API response shapes.
``content_hash`` is shared by the worker (skip logic) and the ``classify`` tool
(stored on ``email_analyses``) so the two paths agree on what "unchanged" means.
"""
import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CategoryEnum(str, Enum):
    """Triage category buckets. Stored verbatim on ``email_analyses.category``."""

    URGENT = "URGENT"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    INVOICES_RECEIPTS = "INVOICES_RECEIPTS"
    SCHEDULING = "SCHEDULING"
    UPDATES_NEWSLETTERS = "UPDATES_NEWSLETTERS"
    GENERAL = "GENERAL"


class ActionItemResult(BaseModel):
    """A single action item extracted from an email."""

    description: str = Field(..., min_length=3, description="Concrete action required")
    deadline: Optional[datetime] = Field(
        default=None,
        description="Due date, if any. Naive datetimes are normalised to UTC.",
    )
    assignee: Optional[str] = Field(default=None, description="Person or team responsible")

    model_config = ConfigDict(extra="forbid")


class ClassificationResult(BaseModel):
    """Classification portion of a triage analysis."""

    message_id: str = Field(..., description="Internal message UUID (string form)")
    category: CategoryEnum = Field(..., description="Triage category")
    urgency_score: int = Field(..., ge=1, le=5, description="Urgency rubric score 1-5")
    rationale: str = Field(..., min_length=1, description="Concise reasoning")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Model confidence 0-1")

    model_config = ConfigDict(extra="forbid")


class EmailAnalysisResult(BaseModel):
    """Full analysis result: classification plus extracted action items."""

    category: CategoryEnum = Field(..., description="Triage category")
    urgency_score: int = Field(..., ge=1, le=5, description="Urgency rubric score 1-5")
    rationale: str = Field(..., description="Concise analysis / summary")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Model confidence 0-1")
    action_items: List[ActionItemResult] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @field_validator("urgency_score")
    @classmethod
    def _check_urgency(cls, v: int) -> int:
        if not 1 <= v <= 5:
            raise ValueError("urgency_score must be between 1 and 5")
        return v


class ThreadBriefing(BaseModel):
    """Synthesized executive briefing for a multi-message thread."""

    summary: str = Field(..., min_length=1, description="Executive summary")
    decisions: List[str] = Field(default_factory=list)
    pending_items: List[str] = Field(default_factory=list)
    participants: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


def content_hash(subject: Optional[str], sender: str, date: datetime, body_text: Optional[str]) -> str:
    """Stable SHA-256 hex digest of a message's identifying content.

    Inputs: subject, sender, date (ISO 8601), and body_text. Used by the
    worker to decide whether to skip re-triage and by the ``classify`` tool to
    stamp ``email_analyses.content_hash``. Re-serialisation of the same values
    yields the same digest; changing the body changes the digest.
    """
    iso_date = date.isoformat() if date else ""
    parts = [
        subject or "",
        sender or "",
        iso_date,
        body_text or "",
    ]
    joined = "\u241f".join(parts)  # UNIT SEPARATOR to avoid field bleed
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def normalise_to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Return ``dt`` as a timezone-aware UTC datetime, or None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
