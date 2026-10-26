"""Webhook endpoints receiving real-time push notifications from Google Cloud Pub/Sub."""
import base64
import json
import logging
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db, get_session_maker
from app.services.ingestion import IngestionService
from app.services.token_store import TokenStore
from app.triage.service import TriageService

logger = logging.getLogger("gmail_assistant.webhook")

router = APIRouter(prefix="/api/v1/webhook", tags=["Webhook"])


async def run_background_sync(email: str) -> None:
    """Execute delta sync in an isolated async session for background webhook processing."""
    session_maker = get_session_maker()
    async with session_maker() as db:
        try:
            account = await TokenStore.get_active_account(db, email=email)
            if account:
                logger.info("Triggering background delta sync for %s", email)
                stats = await IngestionService.delta_sync(db, account)
                logger.info(
                    "Background delta sync finished for %s: %s threads, %s messages",
                    email,
                    stats.threads_synced,
                    stats.messages_synced,
                )
            else:
                return
        except Exception as e:
            logger.error("Background delta sync failed for %s: %s", email, e)
            return

    # Schedule post-ingestion triage for the affected threads (no-op when disabled).
    settings = get_settings()
    if settings.TRIAGE_ENABLED and getattr(stats, "thread_ids", None):
        try:
            await TriageService.triage_after_sync(stats.thread_ids)
        except Exception as e:
            logger.error("Post-sync triage failed for %s: %s", email, e)


@router.post(
    "/pubsub",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Receive Google Cloud Pub/Sub push notification for Gmail updates",
)
async def pubsub_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Handle Google Cloud Pub/Sub push notification.
    Decodes the inner base64 payload containing emailAddress and historyId,
    verifies the account, and schedules delta sync in the background.
    Returns 204 No Content to acknowledge the Pub/Sub message.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    if not isinstance(body, dict) or "message" not in body or not isinstance(body["message"], dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing or invalid 'message' field in Pub/Sub payload",
        )

    raw_data = body["message"].get("data")
    if not raw_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing 'data' in Pub/Sub message",
        )

    try:
        rem = len(raw_data) % 4
        if rem > 0:
            raw_data += "=" * (4 - rem)
        decoded_bytes = base64.b64decode(raw_data)
        notification_data = json.loads(decoded_bytes.decode("utf-8"))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to decode message data: {str(e)}",
        )

    email = notification_data.get("emailAddress")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Decoded notification missing 'emailAddress'",
        )

    account = await TokenStore.get_active_account(db, email=email)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account '{email}' not found or inactive",
        )

    background_tasks.add_task(run_background_sync, email)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
