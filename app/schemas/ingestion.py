"""Pydantic schemas for Gmail ingestion, manual sync, and Pub/Sub webhook payloads."""
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class SyncRequest(BaseModel):
    """Request payload for triggering mailbox synchronization."""

    email: Optional[str] = Field(
        default=None,
        description="Optional account email to sync",
    )
    max_threads: int = Field(
        default=50,
        ge=1,
        le=100,
        description="Max threads to fetch during backfill",
    )


class SyncResponse(BaseModel):
    """Response returned upon sync completion."""

    status: str = Field(default="success", description="Sync status")
    account_email: str = Field(..., description="Target email address")
    threads_synced: int = Field(..., description="Count of threads ingested or updated")
    messages_synced: int = Field(..., description="Count of messages ingested or updated")
    new_history_id: str = Field(..., description="Updated history checkpoint")


class PubSubMessage(BaseModel):
    """Inner message structure from Google Cloud Pub/Sub push notification."""

    data: str = Field(..., description="Base64-encoded notification payload")
    messageId: Optional[str] = Field(
        default=None,
        description="Pub/Sub unique message ID",
    )
    publishTime: Optional[str] = Field(
        default=None,
        description="Publish timestamp",
    )
    attributes: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional message attributes",
    )


class PubSubPushPayload(BaseModel):
    """Google Cloud Pub/Sub push webhook envelope."""

    message: PubSubMessage = Field(..., description="Message object")
    subscription: Optional[str] = Field(
        default=None,
        description="Pub/Sub subscription identifier",
    )


class WatchRegistrationResponse(BaseModel):
    """Response returned when registering Gmail mailbox watch."""

    status: str = Field(default="active", description="Watch registration status")
    history_id: str = Field(..., description="Initial history checkpoint")
    expiration: str = Field(..., description="Watch expiration timestamp in milliseconds")
