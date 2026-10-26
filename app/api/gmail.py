"""Gmail API endpoints for mailbox status, manual sync, and watch registration."""
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.pubsub import PubSubHelper
from app.db.session import get_db
from app.schemas.gmail import GmailStatusResponse
from app.schemas.ingestion import SyncRequest, SyncResponse, WatchRegistrationResponse
from app.services.auth_service import AuthService
from app.services.gmail import GmailClient
from app.services.ingestion import IngestionService
from app.services.token_store import TokenStore
from app.triage.service import TriageService

router = APIRouter(prefix="/api/v1/gmail", tags=["Gmail"])


def _schedule_triage(background_tasks: BackgroundTasks, thread_ids) -> None:
    """Schedule post-sync triage for affected threads (no-op when disabled)."""
    settings = get_settings()
    if not settings.TRIAGE_ENABLED:
        return
    if not thread_ids:
        return
    background_tasks.add_task(TriageService.triage_after_sync, list(thread_ids))


@router.get(
    "/status",
    response_model=GmailStatusResponse,
    summary="Get Gmail mailbox connection status and statistics",
)
async def get_gmail_status(
    email: Optional[str] = Query(default=None, description="Optional email to query specific account"),
    db: AsyncSession = Depends(get_db),
):
    """
    Verify Gmail API connection by querying users.getProfile.
    Returns mailbox message counts, thread counts, and historyId checkpoint.
    """
    try:
        credentials = await AuthService.get_valid_credentials(db=db, email=email)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    try:
        client = GmailClient(credentials=credentials)
        profile = await client.get_profile(user_id="me")
    except PermissionError as pe:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(pe),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to query Gmail API: {str(e)}",
        )

    # If historyId was returned, update account checkpoint in database
    history_id = profile.get("historyId")
    if history_id:
        account = await TokenStore.get_active_account(db, email=email)
        if account:
            account.history_id = str(history_id)
            await db.commit()

    return GmailStatusResponse(
        email=profile.get("emailAddress", ""),
        messages_total=profile.get("messagesTotal", 0),
        threads_total=profile.get("threadsTotal", 0),
        history_id=str(history_id or ""),
        is_connected=True,
    )


@router.post(
    "/sync",
    response_model=SyncResponse,
    summary="Trigger mailbox synchronization (backfill or delta sync)",
)
async def sync_gmail(
    request: Optional[SyncRequest] = None,
    background_tasks: BackgroundTasks = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger manual or on-demand mailbox synchronization.
    If account has a historyId, performs incremental delta sync.
    Otherwise, executes an initial backfill of recent threads.

    Post-sync, affected threads are scheduled for triage in the background
    (no-op when ``TRIAGE_ENABLED`` is false). Per decision D9, an initial
    backfill still reports ``thread_ids`` but triage is only triggered for
    delta-sync additions; the manual triage endpoint or
    ``scripts/verify_triage.py`` should be used to triage backfilled backlog.
    """
    email = request.email if request else None
    max_threads = request.max_threads if request else 50

    account = await TokenStore.get_active_account(db, email=email)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active Google account found. Please login via /auth/login first.",
        )

    was_backfill = not account.history_id
    try:
        if account.history_id:
            stats = await IngestionService.delta_sync(db, account)
        else:
            stats = await IngestionService.initial_backfill(db, account, max_threads=max_threads)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to synchronize Gmail mailbox: {str(e)}",
        )

    # D9: only delta sync auto-triages; initial backfill leaves backlog to the
    # manual endpoint / verify_triage.py.
    if background_tasks is not None and not was_backfill and getattr(stats, "thread_ids", None):
        _schedule_triage(background_tasks, stats.thread_ids)

    return SyncResponse(
        status="success",
        account_email=stats.account_email,
        threads_synced=stats.threads_synced,
        messages_synced=stats.messages_synced,
        new_history_id=stats.new_history_id,
    )


@router.post(
    "/watch",
    response_model=WatchRegistrationResponse,
    summary="Register Gmail mailbox watch with Google Cloud Pub/Sub topic",
)
async def register_gmail_watch(
    email: Optional[str] = Query(default=None, description="Optional email for specific account"),
    db: AsyncSession = Depends(get_db),
):
    """
    Register Gmail push notifications to configured Google Cloud Pub/Sub topic.
    Returns watch expiration timestamp and current historyId checkpoint.
    """
    account = await TokenStore.get_active_account(db, email=email)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active Google account found to register watch.",
        )

    try:
        credentials = await AuthService.get_valid_credentials(db, email=email)
        client = GmailClient(credentials=credentials)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    try:
        topic_path = PubSubHelper.get_topic_path()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pub/Sub configuration error: {str(e)}",
        )

    try:
        watch_result = await client.register_watch(topic_name=topic_path)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to register Gmail watch: {str(e)}",
        )

    history_id = str(watch_result.get("historyId", ""))
    expiration = str(watch_result.get("expiration", ""))

    if history_id and not account.history_id:
        account.history_id = history_id
        await db.commit()

    return WatchRegistrationResponse(
        status="active",
        history_id=history_id,
        expiration=expiration,
    )
