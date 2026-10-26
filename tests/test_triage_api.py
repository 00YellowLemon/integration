"""Tests for the manual triage API endpoint and ingestion wiring.

Patches ``TriageService.triage_message`` / ``triage_after_sync`` to keep the
HTTP layer hermetic (no live Gemini / ADC in CI). The shared ``client`` and
``test_session`` fixtures from ``conftest.py`` use the same in-memory SQLite
session, so seeding via ``test_session`` is visible to the app.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ingestion import SyncStats
from app.triage.service import TriageOutcome
from tests.helpers import seed_account, seed_message, seed_thread


async def _seed(test_session):
    account = await seed_account(test_session)
    thread = await seed_thread(test_session, account)
    message = await seed_message(test_session, thread)
    return account, thread, message


def _make_outcome(message, *, status="ok", skipped=False, error=None, retryable=False):
    return TriageOutcome(
        status=status,
        message_id=str(message.id),
        thread_id=str(message.thread_id),
        category="ACTION_REQUIRED" if status == "ok" else None,
        urgency_score=4 if status == "ok" else None,
        confidence=0.8 if status == "ok" else None,
        action_items_count=2 if status == "ok" else 0,
        skipped=skipped,
        content_hash="abc123",
        error=error,
        retryable=retryable,
    )


@pytest.fixture
async def seeded(client, test_session):
    return await _seed(test_session)


# ---- Manual triage endpoint -------------------------------------------------


@pytest.mark.asyncio
async def test_triage_endpoint_ok(client, test_session, seeded):
    _, _, message = seeded
    outcome = _make_outcome(message)

    with patch("app.api.triage.TriageService.triage_message", new_callable=AsyncMock) as mock_triage, \
         patch("app.api.triage.TriageService.triage_thread", new_callable=AsyncMock):
        mock_triage.return_value = outcome
        response = await client.post(
            f"/api/v1/triage/{message.id}", json={"force": False},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["category"] == "ACTION_REQUIRED"
    assert data["urgency_score"] == 4
    assert data["action_items_count"] == 2
    assert data["skipped"] is False
    assert data["content_hash"] == "abc123"


@pytest.mark.asyncio
async def test_triage_endpoint_unknown_message_404(client):
    response = await client.post(f"/api/v1/triage/{uuid.uuid4()}", json={"force": False})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_triage_endpoint_invalid_uuid_404(client):
    response = await client.post("/api/v1/triage/not-a-uuid", json={"force": False})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_triage_endpoint_disabled_503(client, test_session, seeded):
    from app.core.config import get_settings

    _, _, message = seeded
    settings = get_settings()
    original = settings.TRIAGE_ENABLED
    settings.TRIAGE_ENABLED = False
    try:
        response = await client.post(f"/api/v1/triage/{message.id}", json={"force": False})
        assert response.status_code == 503
    finally:
        settings.TRIAGE_ENABLED = original


@pytest.mark.asyncio
async def test_triage_endpoint_model_failure_502(client, test_session, seeded):
    _, _, message = seeded
    outcome = _make_outcome(message, status="error", error="gemini boom", retryable=True)

    with patch("app.api.triage.TriageService.triage_message", new_callable=AsyncMock) as mock_triage, \
         patch("app.api.triage.TriageService.triage_thread", new_callable=AsyncMock):
        mock_triage.return_value = outcome
        response = await client.post(f"/api/v1/triage/{message.id}", json={"force": False})

    assert response.status_code == 502


@pytest.mark.asyncio
async def test_triage_endpoint_skipped_passes_force_flag(client, test_session, seeded):
    _, _, message = seeded
    outcome = _make_outcome(message, status="skipped", skipped=True)

    with patch("app.api.triage.TriageService.triage_message", new_callable=AsyncMock) as mock_triage, \
         patch("app.api.triage.TriageService.triage_thread", new_callable=AsyncMock):
        mock_triage.return_value = outcome
        response = await client.post(f"/api/v1/triage/{message.id}", json={"force": True})

    assert response.status_code == 200
    assert response.json()["skipped"] is True
    assert mock_triage.call_args.kwargs.get("force") is True


# ---- Ingestion wiring (sync schedules triage) -------------------------------


@pytest.mark.asyncio
async def test_sync_delta_schedules_triage(client, test_session):
    """Delta sync (account has history_id) schedules triage for affected threads."""
    account = await seed_account(test_session, history_id="5000")

    with patch("app.api.gmail.IngestionService.delta_sync", new_callable=AsyncMock) as mock_sync, \
         patch("app.api.gmail.TriageService.triage_after_sync", new_callable=AsyncMock) as mock_triage:
        tid = uuid.uuid4()
        mock_sync.return_value = SyncStats(
            account_email=account.email,
            threads_synced=1,
            messages_synced=2,
            new_history_id="5100",
            thread_ids=[tid],
        )
        response = await client.post("/api/v1/gmail/sync", json={"max_threads": 50})

    assert response.status_code == 200
    assert response.json()["threads_synced"] == 1
    mock_triage.assert_called_once()
    called_thread_ids = mock_triage.call_args.args[0]
    assert tid in called_thread_ids


@pytest.mark.asyncio
async def test_sync_backfill_does_not_schedule_triage(client, test_session):
    """Decision D9: initial backfill reports thread_ids but does not schedule triage."""
    account = await seed_account(test_session, history_id=None)

    with patch("app.api.gmail.IngestionService.initial_backfill", new_callable=AsyncMock) as mock_sync, \
         patch("app.api.gmail.TriageService.triage_after_sync", new_callable=AsyncMock) as mock_triage:
        mock_sync.return_value = SyncStats(
            account_email=account.email,
            threads_synced=1,
            messages_synced=2,
            new_history_id="100",
            thread_ids=[uuid.uuid4()],
        )
        response = await client.post("/api/v1/gmail/sync", json={"max_threads": 50})

    assert response.status_code == 200
    mock_triage.assert_not_called()


@pytest.mark.asyncio
async def test_sync_delta_empty_thread_ids_does_not_schedule_triage(client, test_session):
    """Delta sync with no affected threads schedules nothing."""
    account = await seed_account(test_session, history_id="5000")

    with patch("app.api.gmail.IngestionService.delta_sync", new_callable=AsyncMock) as mock_sync, \
         patch("app.api.gmail.TriageService.triage_after_sync", new_callable=AsyncMock) as mock_triage:
        mock_sync.return_value = SyncStats(
            account_email=account.email,
            threads_synced=0,
            messages_synced=0,
            new_history_id="5000",
            thread_ids=[],
        )
        response = await client.post("/api/v1/gmail/sync", json={"max_threads": 50})

    assert response.status_code == 200
    mock_triage.assert_not_called()


@pytest.mark.asyncio
async def test_pre_existing_suites_import_cleanly():
    """The Phase 1/2 test modules still import without error."""
    import tests.test_auth  # noqa: F401
    import tests.test_config  # noqa: F401
    import tests.test_gmail  # noqa: F401
    import tests.test_ingestion  # noqa: F401
    import tests.test_mime_parser  # noqa: F401
    import tests.test_webhook  # noqa: F401
