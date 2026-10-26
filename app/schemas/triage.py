"""Pydantic schemas for the manual triage API and worker outcomes."""
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class TriageRequest(BaseModel):
    """Request body for ``POST /api/v1/triage/{message_id}``."""

    force: bool = Field(
        default=False,
        description="Re-run even when content_hash is unchanged",
    )

    model_config = ConfigDict(extra="forbid")


class TriageOutcomeResponse(BaseModel):
    """Outcome of a single-message triage run (manual endpoint)."""

    status: str = Field(..., description="ok | skipped | error")
    message_id: str = Field(..., description="Internal message UUID")
    thread_id: Optional[str] = Field(default=None, description="Owning thread UUID")
    category: Optional[str] = Field(default=None, description="Triage category, if classified")
    urgency_score: Optional[int] = Field(default=None, description="Urgency 1-5, if classified")
    confidence: Optional[float] = Field(default=None, description="Model confidence 0-1")
    action_items_count: int = Field(default=0, description="Action items recorded")
    skipped: bool = Field(default=False, description="True when the run was skipped")
    content_hash: Optional[str] = Field(default=None, description="SHA-256 content hash")
    error: Optional[str] = Field(default=None, description="Error message on failure")
    retryable: bool = Field(default=False, description="Whether a retry is meaningful")

    model_config = ConfigDict(from_attributes=True)


class TriageRunSummary(BaseModel):
    """Summary of a thread-scoped triage run (worker)."""

    thread_id: Optional[str] = Field(default=None, description="Internal thread UUID")
    gmail_thread_id: Optional[str] = Field(default=None, description="Gmail thread id")
    status: str = Field(..., description="ok | skipped | error")
    messages_triaged: int = Field(default=0, description="Messages actually (re-)analyzed")
    messages_skipped: int = Field(default=0, description="Messages skipped due to unchanged hash")
    action_items: int = Field(default=0, description="Total action items for the thread")
    synthesis: bool = Field(default=False, description="Whether a synthesis pass ran")
    error: Optional[str] = Field(default=None)
    reason: Optional[str] = Field(default=None, description="Skip reason, if any")

    model_config = ConfigDict(from_attributes=True)
