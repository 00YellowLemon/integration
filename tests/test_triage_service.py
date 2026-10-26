"""Tests for the triage worker: idempotency, force re-run, failure isolation, D9."""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select

from app.models.analysis import ActionItem, EmailAnalysis
from app.models.message import Message
from app.triage.schemas import content_hash
from app.triage.service import TriageService
from tests.fakes import ScriptedChatModel, tool_call_message
from tests.helpers import seed_account, seed_message, seed_thread

from langchain_core.messages import AIMessage


def _classify_args(message):
    return {
        "message_id": str(message.id),
        "category": "ACTION_REQUIRED",
        "urgency_score": 4,
        "rationale": "Needs a reply by Friday",
        "confidence": 0.85,
    }


def _extract_args(message, items=None):
    return {
        "message_id": str(message.id),
        "items": items if items is not None else [{"description": "Reply by Friday"}],
    }


def _build_scripted_model_for_message(message):
    """A fake model that runs inspect -> classify -> extract -> final."""
    return ScriptedChatModel(responses=[
        tool_call_message("inspect_email_payload", {"message_id": str(message.id)}),
        tool_call_message("classify_urgency_and_category", _classify_args(message)),
        tool_call_message("extract_action_items_and_deadlines", _extract_args(message)),
        AIMessage(content="Triage complete."),
    ])


@pytest.fixture
async def seeded(test_session):
    account = await seed_account(test_session)
    thread = await seed_thread(test_session, account)
    message = await seed_message(test_session, thread)
    return account, thread, message


@pytest.mark.asyncio
async def test_first_run_creates_analysis_and_action_items(test_session, seeded):
    account, thread, message = seeded
    model = _build_scripted_model_for_message(message)
    outcome = await TriageService.triage_message(
        test_session, message, model=model, checkpointer=InMemorySaver(),
    )

    assert outcome.status == "ok"
    assert outcome.skipped is False
    assert outcome.category == "ACTION_REQUIRED"
    assert outcome.urgency_score == 4
    assert outcome.action_items_count == 1

    analyses = (await test_session.execute(select(func.count(EmailAnalysis.id)))).scalar()
    items = (await test_session.execute(select(func.count(ActionItem.id)))).scalar()
    assert analyses == 1
    assert items == 1
    assert model.call_count == 4  # inspect, classify, extract, final


@pytest.mark.asyncio
async def test_unchanged_content_hash_is_skipped_with_zero_model_calls(test_session, seeded):
    account, thread, message = seeded
    # Pre-seed an analysis whose content_hash matches the message.
    ch = content_hash(message.subject, message.sender, message.date, message.body_text)
    analysis = EmailAnalysis(
        id=uuid.uuid4(),
        message_id=message.id,
        category="GENERAL",
        urgency_score=2,
        summary="already done",
        confidence=0.5,
        content_hash=ch,
    )
    test_session.add(analysis)
    await test_session.commit()

    model = ScriptedChatModel(responses=[AIMessage(content="should not be called")])
    outcome = await TriageService.triage_message(
        test_session, message, model=model, checkpointer=InMemorySaver(),
    )

    assert outcome.status == "skipped"
    assert outcome.skipped is True
    assert model.call_count == 0  # zero model calls

    analyses = (await test_session.execute(select(func.count(EmailAnalysis.id)))).scalar()
    assert analyses == 1


@pytest.mark.asyncio
async def test_force_reruns_and_updates_existing_row(test_session, seeded):
    account, thread, message = seeded
    ch = content_hash(message.subject, message.sender, message.date, message.body_text)
    analysis = EmailAnalysis(
        id=uuid.uuid4(), message_id=message.id, category="GENERAL",
        urgency_score=2, summary="old", confidence=0.5, content_hash=ch,
    )
    test_session.add(analysis)
    await test_session.commit()
    old_updated_at = analysis.updated_at

    model = _build_scripted_model_for_message(message)
    outcome = await TriageService.triage_message(
        test_session, message, force=True, model=model, checkpointer=InMemorySaver(),
    )

    assert outcome.status == "ok"
    assert outcome.skipped is False
    analyses = (await test_session.execute(select(func.count(EmailAnalysis.id)))).scalar()
    assert analyses == 1  # still exactly one row
    refreshed = (await test_session.execute(select(EmailAnalysis).where(EmailAnalysis.message_id == message.id))).scalar_one()
    assert refreshed.category == "ACTION_REQUIRED"


@pytest.mark.asyncio
async def test_model_exception_rolls_back_and_is_retryable(test_session, seeded):
    account, thread, message = seeded
    model = ScriptedChatModel(responses=[AIMessage(content="x")], raise_exc=RuntimeError("gemini boom"))
    outcome = await TriageService.triage_message(
        test_session, message, model=model, checkpointer=InMemorySaver(),
    )

    assert outcome.status == "error"
    assert outcome.retryable is True
    analyses = (await test_session.execute(select(func.count(EmailAnalysis.id)))).scalar()
    items = (await test_session.execute(select(func.count(ActionItem.id)))).scalar()
    assert analyses == 0  # zero committed analyses (rollback)
    assert items == 0      # zero committed action items


@pytest.mark.asyncio
async def test_initial_backfill_returns_thread_ids_but_no_triage(test_session):
    """Decision D9: initial_backfill reports thread_ids but does NOT triage."""
    from app.services.ingestion import IngestionService
    from tests.test_ingestion import _make_mock_thread  # reuse the Phase 2 helper

    account = await seed_account(test_session, history_id=None)
    mock_threads = [_make_mock_thread("th_d9", ["m_d9_1", "m_d9_2"])]

    with patch("app.services.ingestion.AuthService.get_valid_credentials", new_callable=AsyncMock), \
         patch("app.services.ingestion.GmailClient") as MockClient:
        instance = MockClient.return_value
        instance.list_threads = AsyncMock(return_value={"threads": [{"id": "th_d9"}]})
        instance.get_thread = AsyncMock(return_value=mock_threads[0])
        instance.get_profile = AsyncMock(return_value={"historyId": "9999"})

        with patch("app.triage.service.TriageService.triage_message", new_callable=AsyncMock) as mock_triage:
            stats = await IngestionService.initial_backfill(test_session, account, max_threads=10)

            assert stats.thread_ids is not None
            assert len(stats.thread_ids) == 1
            mock_triage.assert_not_called()  # D9: backfill never triages


@pytest.mark.asyncio
async def test_triage_thread_runs_synthesis_with_test_session_maker(test_session, seeded):
    """Run triage_thread using the test session maker so SQLite is used end-to-end."""
    account, thread, message = seeded

    # Build a fresh per-thread maker bound to the same in-memory engine.
    from sqlalchemy.ext.asyncio import async_sessionmaker

    maker = async_sessionmaker(bind=test_session.bind, expire_on_commit=False)

    model = ScriptedChatModel(responses=[
        tool_call_message("inspect_email_payload", {"message_id": str(message.id)}),
        tool_call_message("classify_urgency_and_category", _classify_args(message)),
        tool_call_message("extract_action_items_and_deadlines", _extract_args(message)),
        AIMessage(content="message done"),
        tool_call_message(
            "synthesize_thread_summary",
            {"thread_id": str(thread.id), "summary": "Thread briefing", "decisions": [], "pending_items": []},
        ),
        AIMessage(content="synthesis done"),
    ])

    summary = await TriageService.triage_thread(
        thread.id, model=model, checkpointer=InMemorySaver(), session_maker=maker,
    )

    assert summary.status == "ok"
    assert summary.messages_triaged == 1
    assert summary.synthesis is True

    async with maker() as db:
        from app.models.thread import Thread
        t = (await db.execute(select(Thread).where(Thread.id == thread.id))).scalar_one()
        assert t.summary is not None
        assert "## Summary" in t.summary


@pytest.mark.asyncio
async def test_triage_after_sync_noop_when_disabled(test_session):
    """triage_after_sync is a no-op when TRIAGE_ENABLED is false."""
    from app.core.config import get_settings

    settings = get_settings()
    original = settings.TRIAGE_ENABLED
    settings.TRIAGE_ENABLED = False
    try:
        with patch("app.triage.service.TriageService.triage_thread", new_callable=AsyncMock) as mock_thread:
            await TriageService.triage_after_sync([uuid.uuid4()])
            mock_thread.assert_not_called()
    finally:
        settings.TRIAGE_ENABLED = original
