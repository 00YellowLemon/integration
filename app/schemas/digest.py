"""Pydantic schemas for Digest and Triage frontend REST endpoints."""
from datetime import datetime
from typing import Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class DigestHighlight(BaseModel):
    """High-urgency email highlight for the daily digest."""

    message_id: str = Field(..., description="Internal message UUID")
    thread_id: str = Field(..., description="Owning thread UUID")
    subject: Optional[str] = Field(None, description="Email subject")
    sender: str = Field(..., description="Email sender")
    date: datetime = Field(..., description="Timestamp of the email")
    category: str = Field(..., description="Assigned category")
    urgency_score: int = Field(..., description="Urgency score (1-5)")
    summary: str = Field(..., description="Brief triage summary")

    model_config = ConfigDict(from_attributes=True)


class CategoryDigest(BaseModel):
    """Category breakdown in the daily digest."""

    category: str = Field(..., description="Category name")
    count: int = Field(..., description="Total emails in category")
    avg_urgency_score: float = Field(..., description="Average urgency score")
    urgent_count: int = Field(default=0, description="Emails with urgency >= 4")

    model_config = ConfigDict(from_attributes=True)


class TodayDigestResponse(BaseModel):
    """Response model for GET /api/v1/digest/today."""

    total_emails: int = Field(..., description="Total emails analyzed in the digest period")
    urgency_distribution: Dict[int, int] = Field(
        ...,
        description="Distribution of urgency scores from 1 to 5",
        json_schema_extra={"example": {1: 5, 2: 8, 3: 12, 4: 4, 5: 2}},
    )
    categories: List[CategoryDigest] = Field(
        default_factory=list,
        description="Category breakdowns",
    )
    urgent_highlights: List[DigestHighlight] = Field(
        default_factory=list,
        description="High urgency emails requiring attention",
    )

    model_config = ConfigDict(from_attributes=True)


class ActionItemFeedItem(BaseModel):
    """Single actionable commitment with owning email/thread context."""

    id: str = Field(..., description="Action item UUID")
    description: str = Field(..., description="Extracted actionable task description")
    deadline: Optional[datetime] = Field(None, description="Extracted deadline if any")
    assignee: Optional[str] = Field(None, description="Extracted assignee if any")
    status: str = Field(..., description="Item status ('pending', 'done', etc.)")
    message_id: str = Field(..., description="Internal message UUID")
    thread_id: str = Field(..., description="Owning thread UUID")
    subject: Optional[str] = Field(None, description="Subject of the source email")

    model_config = ConfigDict(from_attributes=True)


class ActionItemsResponse(BaseModel):
    """Response model for GET /api/v1/action-items."""

    total: int = Field(..., description="Total count matching filters")
    items: List[ActionItemFeedItem] = Field(..., description="List of action items")

    model_config = ConfigDict(from_attributes=True)


class MessageAnalysisItem(BaseModel):
    """Per-message analysis item within a thread briefing."""

    message_id: str = Field(..., description="Internal message UUID")
    subject: Optional[str] = Field(None, description="Message subject")
    sender: str = Field(..., description="Message sender")
    date: datetime = Field(..., description="Timestamp of the message")
    category: Optional[str] = Field(None, description="Classification category")
    urgency_score: Optional[int] = Field(None, description="Urgency score 1-5")
    summary: Optional[str] = Field(None, description="Model summary of the message")

    model_config = ConfigDict(from_attributes=True)


class ThreadTriageResponse(BaseModel):
    """Response model for GET /api/v1/threads/{thread_id}/triage."""

    thread_id: str = Field(..., description="Internal thread UUID")
    gmail_thread_id: str = Field(..., description="Gmail thread ID")
    subject: Optional[str] = Field(None, description="Thread subject")
    executive_summary: Optional[str] = Field(None, description="Synthesized executive briefing")
    max_urgency: int = Field(default=1, description="Highest urgency among messages in thread")
    action_items: List[ActionItemFeedItem] = Field(
        default_factory=list,
        description="Pending and historical action items for thread",
    )
    message_analyses: List[MessageAnalysisItem] = Field(
        default_factory=list,
        description="Chronological triage details for messages in thread",
    )

    model_config = ConfigDict(from_attributes=True)
