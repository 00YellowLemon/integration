"""Manual triage API: re-run triage for a single message and its thread.

The post-ingestion worker is the primary triage path; this endpoint lets an
operator force a re-run of one message and a synthesis pass for its thread.
"""
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.models.message import Message
from app.schemas.triage import TriageOutcomeResponse, TriageRequest
from app.triage.agent import TriageDisabledError
from app.triage.service import TriageOutcome, TriageService

logger = logging.getLogger("gmail_assistant.api.triage")

router = APIRouter(prefix="/api/v1/triage", tags=["Triage"])


def _to_response(outcome: TriageOutcome) -> TriageOutcomeResponse:
    return TriageOutcomeResponse(
        status=outcome.status,
        message_id=outcome.message_id,
        thread_id=outcome.thread_id,
        category=outcome.category,
        urgency_score=outcome.urgency_score,
        confidence=outcome.confidence,
        action_items_count=outcome.action_items_count,
        skipped=outcome.skipped,
        content_hash=outcome.content_hash,
        error=outcome.error,
        retryable=outcome.retryable,
    )


@router.post(
    "/{message_id}",
    response_model=TriageOutcomeResponse,
    summary="Run triage for a single message and synthesise its thread",
)
async def triage_message(
    message_id: str,
    body: TriageRequest | None = None,
    background_tasks: BackgroundTasks = None,
    db: AsyncSession = Depends(get_db),
):
    """Re-run triage for ``message_id`` (optionally forced) and schedule synthesis.

    - 404 if the message is unknown.
    - 503 if triage is disabled.
    - 502 on model failure (retryable).
    """
    try:
        msg_uuid = uuid.UUID(message_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown message_id: {message_id}",
        )

    result = await db.execute(select(Message).where(Message.id == msg_uuid))
    message = result.scalar_one_or_none()
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown message_id: {message_id}",
        )

    if not get_settings().TRIAGE_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Triage is disabled (TRIAGE_ENABLED=false).",
        )

    force = bool(body.force) if body else False

    try:
        outcome = await TriageService.triage_message(db, message, force=force)
    except TriageDisabledError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except Exception as e:
        logger.error("Manual triage failed for %s: %s", message_id, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Triage model failure: {e}. Retry later.",
        )

    # Schedule a thread synthesis pass (no-op when disabled).
    if background_tasks is not None and outcome.thread_id:
        try:
            thread_uuid = uuid.UUID(outcome.thread_id)
        except (ValueError, TypeError):
            thread_uuid = None
        if thread_uuid is not None:
            background_tasks.add_task(TriageService.triage_thread, thread_uuid, force=force)

    if outcome.status == "error" and outcome.retryable:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=outcome.error or "Triage model failure.",
        )

    return _to_response(outcome)
