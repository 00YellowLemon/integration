"""Tests for triage Pydantic schemas and the per-run tool factory."""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.models.analysis import ActionItem, EmailAnalysis
from app.models.thread import Thread
from app.triage.schemas import (
    ActionItemResult,
    CategoryEnum,
    ClassificationResult,
    EmailAnalysisResult,
    content_hash,
)
from app.triage.tools import TriageRunState, build_triage_tools
from tests.helpers import seed_account, seed_message, seed_thread


# ---------------------------------------------------------------------------
# Schema validation (validation.md §2.1)
# ---------------------------------------------------------------------------


def test_category_enum_rejects_unknown_and_accepts_all_six():
    valid = {c.value for c in CategoryEnum}
    assert valid == {
        "URGENT", "ACTION_REQUIRED", "INVOICES_RECEIPTS",
        "SCHEDULING", "UPDATES_NEWSLETTERS", "GENERAL",
    }
    with pytest.raises(ValueError):
        CategoryEnum("NOPE")


def test_urgency_score_bounds():
    ClassificationResult(
        message_id="x", category=CategoryEnum.GENERAL,
        urgency_score=1, rationale="r", confidence=0.5,
    )
    ClassificationResult(
        message_id="x", category=CategoryEnum.GENERAL,
        urgency_score=5, rationale="r", confidence=0.5,
    )
    with pytest.raises(ValueError):
        ClassificationResult(
            message_id="x", category=CategoryEnum.GENERAL,
            urgency_score=0, rationale="r", confidence=0.5,
        )
    with pytest.raises(ValueError):
        ClassificationResult(
            message_id="x", category=CategoryEnum.GENERAL,
            urgency_score=6, rationale="r", confidence=0.5,
        )


def test_confidence_bounds():
    with pytest.raises(ValueError):
        ClassificationResult(
            message_id="x", category=CategoryEnum.GENERAL,
            urgency_score=3, rationale="r", confidence=1.5,
        )
    with pytest.raises(ValueError):
        ClassificationResult(
            message_id="x", category=CategoryEnum.GENERAL,
            urgency_score=3, rationale="r", confidence=-0.1,
        )


def test_action_item_result_description_min_length_and_optional_fields():
    with pytest.raises(ValueError):
        ActionItemResult(description="ab")
    item = ActionItemResult(description="Valid action item description", deadline=None, assignee=None)
    assert item.deadline is None
    assert item.assignee is None


def test_content_hash_stable_and_changes_with_body():
    dt = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    h1 = content_hash("Subj", "a@b.com", dt, "body one")
    h2 = content_hash("Subj", "a@b.com", dt, "body one")
    assert h1 == h2
    h3 = content_hash("Subj", "a@b.com", dt, "body two")
    assert h1 != h3
    assert len(h1) == 64  # sha256 hex


def test_email_analysis_result_validates():
    res = EmailAnalysisResult(
        category=CategoryEnum.ACTION_REQUIRED,
        urgency_score=4,
        rationale="Needs reply",
        confidence=0.9,
        action_items=[ActionItemResult(description="Reply by Friday")],
    )
    assert res.urgency_score == 4
    with pytest.raises(ValueError):
        EmailAnalysisResult(
            category=CategoryEnum.GENERAL, urgency_score=9, rationale="x", confidence=0.5,
        )


# ---------------------------------------------------------------------------
# Tool behaviour (validation.md §2.2)
# ---------------------------------------------------------------------------


@pytest.fixture
async def seeded_message(test_session):
    account = await seed_account(test_session)
    thread = await seed_thread(test_session, account)
    message = await seed_message(test_session, thread)
    return account, thread, message


@pytest.mark.asyncio
async def test_inspect_email_payload_returns_compact_payload(test_session, seeded_message):
    _, thread, message = seeded_message
    tools = build_triage_tools(test_session, TriageRunState(message_id=str(message.id), thread_id=str(thread.id)))
    inspect = tools[0]
    result = await inspect.ainvoke({"message_id": str(message.id)})

    assert result["message_id"] == str(message.id)
    assert result["thread_id"] == str(thread.id)
    assert result["subject"] == message.subject
    assert result["sender"] == message.sender
    assert result["date"] == message.date.isoformat()
    assert isinstance(result["body_text"], str)
    assert result["body_text"].startswith("Please review")
    assert result["prior_context"] == []


@pytest.mark.asyncio
async def test_inspect_email_payload_truncates_long_body(test_session, seeded_message):
    from app.core.config import get_settings
    max_chars = get_settings().TRIAGE_MAX_BODY_CHARS

    _, thread, message = seeded_message
    message.body_text = "x" * (max_chars + 500)
    test_session.add(message)
    await test_session.commit()

    tools = build_triage_tools(test_session, TriageRunState(message_id=str(message.id), thread_id=str(thread.id)))
    result = await tools[0].ainvoke({"message_id": str(message.id)})
    assert len(result["body_text"]) <= max_chars + 1  # +1 for ellipsis char


@pytest.mark.asyncio
async def test_inspect_email_payload_raises_for_unknown_message(test_session):
    tools = build_triage_tools(test_session, TriageRunState())
    with pytest.raises(Exception):
        await tools[0].ainvoke({"message_id": str(uuid.uuid4())})


@pytest.mark.asyncio
async def test_classify_inserts_then_updates_same_row(test_session, seeded_message):
    _, thread, message = seeded_message
    tools = build_triage_tools(test_session, TriageRunState(message_id=str(message.id), thread_id=str(thread.id)))
    classify = tools[1]

    await classify.ainvoke({
        "message_id": str(message.id),
        "category": "ACTION_REQUIRED",
        "urgency_score": 4,
        "rationale": "Needs a reply",
        "confidence": 0.8,
    })
    await test_session.commit()

    count_after_first = (await test_session.execute(select(func.count(EmailAnalysis.id)))).scalar()
    assert count_after_first == 1

    # Second call with a different category upserts the same row (count stays 1).
    await classify.ainvoke({
        "message_id": str(message.id),
        "category": "URGENT",
        "urgency_score": 5,
        "rationale": "Now urgent",
        "confidence": 0.9,
    })
    await test_session.commit()

    count_after_second = (await test_session.execute(select(func.count(EmailAnalysis.id)))).scalar()
    assert count_after_second == 1

    analysis = (await test_session.execute(select(EmailAnalysis).where(EmailAnalysis.message_id == message.id))).scalar_one()
    assert analysis.category == "URGENT"
    assert analysis.urgency_score == 5
    assert analysis.content_hash  # populated


@pytest.mark.asyncio
async def test_extract_action_items_refuses_before_classification(test_session, seeded_message):
    _, thread, message = seeded_message
    tools = build_triage_tools(test_session, TriageRunState(message_id=str(message.id), thread_id=str(thread.id)))
    extract = tools[2]

    with pytest.raises(Exception):
        await extract.ainvoke({"message_id": str(message.id), "items": [{"description": "Do something"}]})


@pytest.mark.asyncio
async def test_extract_action_items_replaces_on_rerun(test_session, seeded_message):
    _, thread, message = seeded_message
    tools = build_triage_tools(test_session, TriageRunState(message_id=str(message.id), thread_id=str(thread.id)))
    classify = tools[1]
    extract = tools[2]

    await classify.ainvoke({
        "message_id": str(message.id), "category": "ACTION_REQUIRED",
        "urgency_score": 4, "rationale": "Needs action", "confidence": 0.8,
    })
    await test_session.commit()

    await extract.ainvoke({
        "message_id": str(message.id),
        "items": [
            {"description": "Reply by Friday", "deadline": "2026-10-30T17:00:00", "assignee": "me"},
            {"description": "Book the room"},
        ],
    })
    await test_session.commit()

    analysis = (await test_session.execute(select(EmailAnalysis).where(EmailAnalysis.message_id == message.id))).scalar_one()
    first_count = (await test_session.execute(select(func.count(ActionItem.id)).where(ActionItem.analysis_id == analysis.id))).scalar()
    assert first_count == 2
    # Naive deadline was normalised to timezone-aware UTC.
    first_item = (await test_session.execute(select(ActionItem).where(ActionItem.analysis_id == analysis.id))).scalars().first()
    assert first_item.deadline.tzinfo is not None

    # Re-run with a different list -> replace semantics (no duplicates).
    await extract.ainvoke({
        "message_id": str(message.id),
        "items": [{"description": "Only this one remains"}],
    })
    await test_session.commit()

    second_count = (await test_session.execute(select(func.count(ActionItem.id)).where(ActionItem.analysis_id == analysis.id))).scalar()
    assert second_count == 1


@pytest.mark.asyncio
async def test_synthesize_thread_summary_writes_markdown_sections(test_session, seeded_message):
    _, thread, message = seeded_message
    tools = build_triage_tools(test_session, TriageRunState(thread_id=str(thread.id)))
    synth = tools[3]

    await synth.ainvoke({
        "thread_id": str(thread.id),
        "summary": "Team aligned on Q4 priorities.",
        "decisions": ["Ship feature A in November"],
        "pending_items": ["Confirm budget"],
    })
    await test_session.commit()

    refreshed = (await test_session.execute(select(Thread).where(Thread.id == thread.id))).scalar_one()
    assert refreshed.summary is not None
    assert "## Summary" in refreshed.summary
    assert "## Decisions" in refreshed.summary
    assert "## Pending" in refreshed.summary
    assert "Ship feature A in November" in refreshed.summary


@pytest.mark.asyncio
async def test_synthesize_thread_summary_accepts_gmail_thread_id(test_session, seeded_message):
    _, thread, message = seeded_message
    tools = build_triage_tools(test_session, TriageRunState())
    await tools[3].ainvoke({
        "thread_id": thread.gmail_thread_id,
        "summary": "Briefing by gmail id",
    })
    await test_session.commit()
    refreshed = (await test_session.execute(select(Thread).where(Thread.id == thread.id))).scalar_one()
    assert "## Summary" in refreshed.summary


@pytest.mark.asyncio
async def test_synthesize_thread_summary_rejects_empty_summary(test_session, seeded_message):
    from app.models.thread import Thread
    _, thread, message = seeded_message
    tools = build_triage_tools(test_session, TriageRunState())
    with pytest.raises(Exception):
        await tools[3].ainvoke({"thread_id": str(thread.id), "summary": "   "})
