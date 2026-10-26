"""Pydantic schemas for Gmail API status and mailbox statistics."""
from pydantic import BaseModel, Field


class GmailStatusResponse(BaseModel):
    """Response returned by Gmail connection status endpoint."""
    email: str = Field(..., description="Authenticated Gmail email address")
    messages_total: int = Field(..., description="Total message count in mailbox")
    threads_total: int = Field(..., description="Total thread count in mailbox")
    history_id: str = Field(..., description="Current mailbox synchronization cursor")
    is_connected: bool = Field(default=True, description="Connection health status")
