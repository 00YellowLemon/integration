"""Digest & Triage REST APIs for frontend consumption.

Exposes high-speed read endpoints for daily summaries, pending action items,
and thread executive briefings:
- ``GET /api/v1/digest/today``: Daily triage digest with urgency distribution and highlights.
- ``GET /api/v1/action-items``: Filterable feed of pending action items with source message context.
- ``GET /api/v1/threads/{thread_id}/triage``: Comprehensive thread executive briefing, actions, and messages.
"""
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, List, Optional

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.analysis import ActionItem, EmailAnalysis
from app.models.message import Message
from app.models.thread import Thread
from app.schemas.digest import (
    ActionItemFeedItem,
    ActionItemsResponse,
    CategoryDigest,
    DigestHighlight,
    MessageAnalysisItem,
    ThreadTriageResponse,
    TodayDigestResponse,
)

logger = logging.getLogger("gmail_assistant.api.digest")

router = APIRouter(prefix="/api/v1", tags=["Digest & Action Items"])


@router.get(
    "/digest/today",
    response_model=TodayDigestResponse,
    summary="Get today's triage digest",
)
async def get_today_digest(
    hours: int = Query(default=24, ge=1, le=168, description="Lookback window in hours"),
    db: AsyncSession = Depends(get_db),
) -> TodayDigestResponse:
    """Retrieve daily email digest with urgency distribution, category breakdowns, and highlights."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Fetch analyses within the lookback window joined to their messages
    stmt = (
        select(EmailAnalysis, Message)
        .join(Message, Message.id == EmailAnalysis.message_id)
        .where(
            (Message.date >= cutoff) | (EmailAnalysis.created_at >= cutoff)
        )
        .order_by(EmailAnalysis.urgency_score.desc(), Message.date.desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    total_emails = len(rows)
    urgency_distribution: Dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    category_map: Dict[str, Dict[str, Any]] = {}
    urgent_highlights: List[DigestHighlight] = []

    for analysis, message in rows:
        score = analysis.urgency_score
        if score in urgency_distribution:
            urgency_distribution[score] += 1
        else:
            urgency_distribution[score] = 1

        cat = analysis.category or "UNCATEGORIZED"
        if cat not in category_map:
            category_map[cat] = {
                "count": 0,
                "urgency_sum": 0,
                "urgent_count": 0,
            }
        category_map[cat]["count"] += 1
        category_map[cat]["urgency_sum"] += score
        if score >= 4 or cat in ("URGENT", "ACTION_REQUIRED"):
            category_map[cat]["urgent_count"] += 1
            if len(urgent_highlights) < 25:
                urgent_highlights.append(
                    DigestHighlight(
                        message_id=str(message.id),
                        thread_id=str(message.thread_id),
                        subject=message.subject,
                        sender=message.sender,
                        date=message.date,
                        category=cat,
                        urgency_score=score,
                        summary=analysis.summary,
                    )
                )

    categories_list: List[CategoryDigest] = []
    for cat, stats in sorted(category_map.items()):
        avg_score = round(stats["urgency_sum"] / stats["count"], 2) if stats["count"] > 0 else 0.0
        categories_list.append(
            CategoryDigest(
                category=cat,
                count=stats["count"],
                avg_urgency_score=avg_score,
                urgent_count=stats["urgent_count"],
            )
        )

    return TodayDigestResponse(
        total_emails=total_emails,
        urgency_distribution=urgency_distribution,
        categories=categories_list,
        urgent_highlights=urgent_highlights,
    )


@router.get(
    "/action-items",
    response_model=ActionItemsResponse,
    summary="Get actionable commitments feed",
)
async def list_action_items(
    status_filter: Optional[str] = Query(
        default="pending",
        alias="status",
        description="Filter by status ('pending', 'done', 'all')",
    ),
    has_deadline: Optional[bool] = Query(
        default=None,
        description="Filter by whether a deadline is present",
    ),
    limit: int = Query(default=50, ge=1, le=200, description="Pagination limit"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    db: AsyncSession = Depends(get_db),
) -> ActionItemsResponse:
    """Retrieve pending or all action items with owning email subject and thread ID."""
    base_query = (
        select(ActionItem, Message.thread_id, Message.subject)
        .join(Message, Message.id == ActionItem.message_id)
    )

    if status_filter and status_filter.lower() != "all":
        base_query = base_query.where(ActionItem.status == status_filter.lower())

    if has_deadline is True:
        base_query = base_query.where(ActionItem.deadline.isnot(None))
    elif has_deadline is False:
        base_query = base_query.where(ActionItem.deadline.is_(None))

    count_query = select(func.count()).select_from(base_query.subquery())
    total_result = await db.execute(count_query)
    total_count = int(total_result.scalar() or 0)

    # Order by deadline ascending (nulls last), then created_at descending
    ordered_query = (
        base_query.order_by(
            ActionItem.deadline.asc().nulls_last(),
            ActionItem.created_at.desc(),
        )
        .offset(offset)
        .limit(limit)
    )

    result = await db.execute(ordered_query)
    items: List[ActionItemFeedItem] = []
    for item, thread_id, subject in result.all():
        items.append(
            ActionItemFeedItem(
                id=str(item.id),
                description=item.description,
                deadline=item.deadline,
                assignee=item.assignee,
                status=item.status,
                message_id=str(item.message_id),
                thread_id=str(thread_id),
                subject=subject,
            )
        )

    return ActionItemsResponse(total=total_count, items=items)


@router.get(
    "/threads/{thread_id}/triage",
    response_model=ThreadTriageResponse,
    summary="Get thread executive briefing and message triage",
)
async def get_thread_triage(
    thread_id: str,
    db: AsyncSession = Depends(get_db),
) -> ThreadTriageResponse:
    """Retrieve synthesized executive briefing, decisions, and message analyses for a thread.

    Accepts either an internal UUID or a Gmail thread ID.
    """
    thread = None
    try:
        thread_uuid = uuid.UUID(thread_id)
        res = await db.execute(
            select(Thread)
            .where(Thread.id == thread_uuid)
            .options(selectinload(Thread.messages).selectinload(Message.analysis))
        )
        thread = res.scalar_one_or_none()
    except (ValueError, TypeError):
        pass

    if thread is None:
        res = await db.execute(
            select(Thread)
            .where(Thread.gmail_thread_id == thread_id)
            .options(selectinload(Thread.messages).selectinload(Message.analysis))
        )
        thread = res.scalar_one_or_none()

    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread not found: {thread_id}",
        )

    # Sort messages chronologically
    sorted_messages = sorted(thread.messages, key=lambda m: m.date)

    max_urgency = 1
    message_analyses: List[MessageAnalysisItem] = []
    message_ids = [m.id for m in sorted_messages]

    for m in sorted_messages:
        analysis = m.analysis
        if analysis:
            if analysis.urgency_score > max_urgency:
                max_urgency = analysis.urgency_score
            message_analyses.append(
                MessageAnalysisItem(
                    message_id=str(m.id),
                    subject=m.subject,
                    sender=m.sender,
                    date=m.date,
                    category=analysis.category,
                    urgency_score=analysis.urgency_score,
                    summary=analysis.summary,
                )
            )
        else:
            message_analyses.append(
                MessageAnalysisItem(
                    message_id=str(m.id),
                    subject=m.subject,
                    sender=m.sender,
                    date=m.date,
                    category=None,
                    urgency_score=None,
                    summary=None,
                )
            )

    # Fetch action items for this thread's messages
    action_items: List[ActionItemFeedItem] = []
    if message_ids:
        ai_res = await db.execute(
            select(ActionItem, Message.subject)
            .join(Message, Message.id == ActionItem.message_id)
            .where(ActionItem.message_id.in_(message_ids))
            .order_by(ActionItem.deadline.asc().nulls_last(), ActionItem.created_at.desc())
        )
        for item, subject in ai_res.all():
            action_items.append(
                ActionItemFeedItem(
                    id=str(item.id),
                    description=item.description,
                    deadline=item.deadline,
                    assignee=item.assignee,
                    status=item.status,
                    message_id=str(item.message_id),
                    thread_id=str(thread.id),
                    subject=subject,
                )
            )

    return ThreadTriageResponse(
        thread_id=str(thread.id),
        gmail_thread_id=thread.gmail_thread_id,
        subject=thread.subject,
        executive_summary=thread.summary,
        max_urgency=max_urgency,
        action_items=action_items,
        message_analyses=message_analyses,
    )
