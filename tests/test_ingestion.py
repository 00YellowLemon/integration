"""Integration and idempotency tests for Gmail email ingestion and delta synchronization."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
import uuid
import pytest
from sqlalchemy import func, select

from app.models.account import Account
from app.models.message import Message
from app.models.thread import Thread
from app.services.ingestion import IngestionService
from app.services.mime_parser import MIMEParser
from app.services.token_store import TokenStore


def _encode_b64(text: str) -> str:
    import base64
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("utf-8").rstrip("=")


@pytest.fixture
async def sample_account(test_session):
    """Seed an active Google account for testing."""
    account = await TokenStore.save_tokens(
        db=test_session,
        email="user@example.com",
        access_token="mock-access-token",
        refresh_token="mock-refresh-token",
        expiry=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    account.history_id = "1000"
    await test_session.commit()
    await test_session.refresh(account)
    return account



def _make_mock_thread(thread_id: str, message_ids: list[str]) -> dict:
    """Construct mock Gmail thread resource with full message details."""
    messages = []
    for idx, mid in enumerate(message_ids):
        messages.append({
            "id": mid,
            "threadId": thread_id,
            "snippet": f"Snippet for {mid}",
            "internalDate": str(1729680000000 + idx * 1000),
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": f"sender{idx}@example.com"},
                    {"name": "To", "value": "user@example.com"},
                    {"name": "Subject", "value": "Test Subject"},
                    {"name": "Date", "value": "Fri, 23 Oct 2026 10:00:00 +0000"},
                ],
                "body": {
                    "data": _encode_b64(f"Message body content for {mid}"),
                    "size": 30,
                },
            },
        })
    return {
        "id": thread_id,
        "historyId": "1001",
        "messages": messages,
        "snippet": f"Snippet for {message_ids[-1]}",
    }


@pytest.mark.asyncio
async def test_thread_and_message_creation(test_session, sample_account):
    """Test creating a thread and linked messages from Gmail thread resource."""
    thread_payload = _make_mock_thread("th_100", ["msg_001", "msg_002"])

    thread, count = await IngestionService.ingest_thread(test_session, sample_account.id, thread_payload)
    await test_session.commit()

    assert thread.gmail_thread_id == "th_100"
    assert count == 2
    assert thread.subject == "Test Subject"

    # Query database to confirm persisted rows
    threads = (await test_session.execute(select(Thread))).scalars().all()
    messages = (await test_session.execute(select(Message))).scalars().all()

    assert len(threads) == 1
    assert len(messages) == 2
    assert messages[0].thread_id == thread.id
    assert messages[1].thread_id == thread.id


@pytest.mark.asyncio
async def test_ingestion_idempotency(test_session, sample_account):
    """Re-ingesting the exact same thread and message payload causes zero duplicate rows."""
    thread_payload = _make_mock_thread("th_200", ["msg_201", "msg_202"])

    # First ingestion
    _, count1 = await IngestionService.ingest_thread(test_session, sample_account.id, thread_payload)
    await test_session.commit()
    assert count1 == 2

    # Second ingestion of the same payload
    _, count2 = await IngestionService.ingest_thread(test_session, sample_account.id, thread_payload)
    await test_session.commit()
    assert count2 == 2

    thread_count = (await test_session.execute(select(func.count(Thread.id)))).scalar()
    message_count = (await test_session.execute(select(func.count(Message.id)))).scalar()

    assert thread_count == 1
    assert message_count == 2


@pytest.mark.asyncio
async def test_initial_backfill(test_session, sample_account):
    """Test initial backfill populating threads and messages and updating history_id."""
    mock_threads = [_make_mock_thread("th_backfill_1", ["m1", "m2"])]

    with patch("app.services.ingestion.AuthService.get_valid_credentials", new_callable=AsyncMock) as mock_creds, \
         patch("app.services.ingestion.GmailClient") as MockClient:
        instance = MockClient.return_value
        instance.list_threads = AsyncMock(return_value={"threads": [{"id": "th_backfill_1"}]})
        instance.get_thread = AsyncMock(return_value=mock_threads[0])
        instance.get_profile = AsyncMock(return_value={"historyId": "1050"})

        stats = await IngestionService.initial_backfill(test_session, sample_account, max_threads=50)

        assert stats.threads_synced == 1
        assert stats.messages_synced == 2
        assert stats.new_history_id == "1050"

        await test_session.refresh(sample_account)
        assert sample_account.history_id == "1050"


@pytest.mark.asyncio
async def test_delta_sync_processing(test_session, sample_account):
    """Test delta sync pulling only updated threads based on history list."""
    new_thread = _make_mock_thread("th_delta_1", ["m_delta_1"])

    with patch("app.services.ingestion.AuthService.get_valid_credentials", new_callable=AsyncMock), \
         patch("app.services.ingestion.GmailClient") as MockClient:
        instance = MockClient.return_value
        instance.list_history = AsyncMock(return_value={
            "history": [
                {
                    "messagesAdded": [
                        {"message": {"id": "m_delta_1", "threadId": "th_delta_1"}}
                    ]
                }
            ],
            "historyId": "1100",
        })
        instance.get_thread = AsyncMock(return_value=new_thread)

        stats = await IngestionService.delta_sync(test_session, sample_account)

        assert stats.threads_synced == 1
        assert stats.messages_synced == 1
        assert stats.new_history_id == "1100"

        await test_session.refresh(sample_account)
        assert sample_account.history_id == "1100"


@pytest.mark.asyncio
async def test_history_id_expiry_recovery(test_session, sample_account):
    """Test delta sync recovers from expired historyId by falling back to initial backfill."""
    mock_thread = _make_mock_thread("th_recovery_1", ["m_rec_1"])

    with patch("app.services.ingestion.AuthService.get_valid_credentials", new_callable=AsyncMock), \
         patch("app.services.ingestion.GmailClient") as MockClient:
        instance = MockClient.return_value
        # Simulate Gmail API 404 / historyId expired error
        instance.list_history = AsyncMock(side_effect=Exception("HTTP 404: historyId not found"))
        instance.list_threads = AsyncMock(return_value={"threads": [{"id": "th_recovery_1"}]})
        instance.get_thread = AsyncMock(return_value=mock_thread)
        instance.get_profile = AsyncMock(return_value={"historyId": "2000"})

        stats = await IngestionService.delta_sync(test_session, sample_account)

        assert stats.threads_synced == 1
        assert stats.messages_synced == 1
        assert stats.new_history_id == "2000"

        await test_session.refresh(sample_account)
        assert sample_account.history_id == "2000"
