"""Unit tests for Digest & Triage REST APIs (/api/v1/digest/today, /api/v1/action-items, /api/v1/threads/{thread_id}/triage)."""
from datetime import datetime, timedelta, timezone
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.helpers import (
    seed_account,
    seed_action_item,
    seed_analysis,
    seed_message,
    seed_thread,
)


@pytest.mark.asyncio
async def test_get_today_digest_empty(client: AsyncClient, test_session: AsyncSession):
    """GET /api/v1/digest/today returns zero counts when no emails exist."""
    resp = await client.get("/api/v1/digest/today")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_emails"] == 0
    assert data["urgency_distribution"] == {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
    assert data["categories"] == []
    assert data["urgent_highlights"] == []


@pytest.mark.asyncio
async def test_get_today_digest_with_data(client: AsyncClient, test_session: AsyncSession):
    """GET /api/v1/digest/today aggregates urgency scores, categories, and urgent highlights."""
    account = await seed_account(test_session, email="digest@example.com")
    thread = await seed_thread(test_session, account, gmail_thread_id="th_digest_1")

    # Message 1: Urgent (urgency 5)
    now = datetime.now(timezone.utc)
    m1 = await seed_message(
        test_session,
        thread,
        gmail_message_id="msg_d1",
        subject="Urgent Security Alert",
        date=now - timedelta(hours=2),
    )
    await seed_analysis(
        test_session,
        m1,
        category="URGENT",
        urgency_score=5,
        summary="Critical vulnerability reported in production.",
    )

    # Message 2: Newsletter (urgency 1)
    m2 = await seed_message(
        test_session,
        thread,
        gmail_message_id="msg_d2",
        subject="Weekly Tech Digest",
        date=now - timedelta(hours=5),
    )
    await seed_analysis(
        test_session,
        m2,
        category="NEWSLETTER",
        urgency_score=1,
        summary="Weekly roundup of software updates.",
    )

    # Message 3: Action Required (urgency 4)
    m3 = await seed_message(
        test_session,
        thread,
        gmail_message_id="msg_d3",
        subject="Invoice Approval Needed",
        date=now - timedelta(hours=1),
    )
    await seed_analysis(
        test_session,
        m3,
        category="ACTION_REQUIRED",
        urgency_score=4,
        summary="Please approve Q3 marketing invoice.",
    )

    # Query endpoint
    resp = await client.get("/api/v1/digest/today")
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_emails"] == 3
    assert data["urgency_distribution"]["5"] == 1
    assert data["urgency_distribution"]["4"] == 1
    assert data["urgency_distribution"]["1"] == 1
    assert data["urgency_distribution"]["2"] == 0
    assert data["urgency_distribution"]["3"] == 0

    assert len(data["categories"]) == 3
    cat_names = [c["category"] for c in data["categories"]]
    assert "URGENT" in cat_names
    assert "ACTION_REQUIRED" in cat_names
    assert "NEWSLETTER" in cat_names

    # Urgent highlights should contain messages with score >= 4
    assert len(data["urgent_highlights"]) == 2
    highlight_subjects = [h["subject"] for h in data["urgent_highlights"]]
    assert "Urgent Security Alert" in highlight_subjects
    assert "Invoice Approval Needed" in highlight_subjects


@pytest.mark.asyncio
async def test_action_items_endpoint(client: AsyncClient, test_session: AsyncSession):
    """GET /api/v1/action-items supports filtering by status, deadline, and pagination."""
    account = await seed_account(test_session, email="actions@example.com")
    thread = await seed_thread(test_session, account, gmail_thread_id="th_actions_1")
    msg = await seed_message(test_session, thread, gmail_message_id="msg_act_1", subject="Team Standup")
    analysis = await seed_analysis(test_session, msg)

    now = datetime.now(timezone.utc)
    # Pending item with near deadline
    item1 = await seed_action_item(
        test_session,
        analysis,
        msg,
        description="Submit budget estimate",
        deadline=now + timedelta(days=1),
        status="pending",
    )
    # Pending item with far deadline
    item2 = await seed_action_item(
        test_session,
        analysis,
        msg,
        description="Draft annual review",
        deadline=now + timedelta(days=10),
        status="pending",
    )
    # Done item
    item3 = await seed_action_item(
        test_session,
        analysis,
        msg,
        description="Book conference tickets",
        deadline=now - timedelta(days=1),
        status="done",
    )
    # Pending item with no deadline
    item4 = await seed_action_item(
        test_session,
        analysis,
        msg,
        description="Organize design files",
        deadline=None,
        status="pending",
    )

    # 1. Default pending filter: should return item1, item2, item4
    resp = await client.get("/api/v1/action-items")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    descriptions = [i["description"] for i in data["items"]]
    assert "Submit budget estimate" in descriptions
    assert "Draft annual review" in descriptions
    assert "Organize design files" in descriptions
    assert "Book conference tickets" not in descriptions

    # Verify deadline order: item1 (1 day) should precede item2 (10 days)
    assert descriptions.index("Submit budget estimate") < descriptions.index("Draft annual review")

    # 2. Filter by status=done
    resp_done = await client.get("/api/v1/action-items?status=done")
    assert resp_done.status_code == 200
    data_done = resp_done.json()
    assert data_done["total"] == 1
    assert data_done["items"][0]["description"] == "Book conference tickets"

    # 3. Filter by has_deadline=true
    resp_dl = await client.get("/api/v1/action-items?status=pending&has_deadline=true")
    assert resp_dl.status_code == 200
    data_dl = resp_dl.json()
    assert data_dl["total"] == 2
    assert all(i["deadline"] is not None for i in data_dl["items"])

    # 4. Filter by has_deadline=false
    resp_nodl = await client.get("/api/v1/action-items?status=pending&has_deadline=false")
    assert resp_nodl.status_code == 200
    data_nodl = resp_nodl.json()
    assert data_nodl["total"] == 1
    assert data_nodl["items"][0]["description"] == "Organize design files"

    # 5. Filter status=all
    resp_all = await client.get("/api/v1/action-items?status=all")
    assert resp_all.status_code == 200
    assert resp_all.json()["total"] == 4


@pytest.mark.asyncio
async def test_thread_triage_endpoint_by_internal_uuid(
    client: AsyncClient,
    test_session: AsyncSession,
):
    """GET /api/v1/threads/{thread_id}/triage resolves by internal UUID and returns executive briefing."""
    account = await seed_account(test_session, email="triage_thread@example.com")
    thread = await seed_thread(
        test_session,
        account,
        gmail_thread_id="gmail_th_xyz",
        subject="Project Alpha Launch",
        summary="Executive summary: Alpha launch approved for Q4.",
    )
    now = datetime.now(timezone.utc)
    m1 = await seed_message(
        test_session,
        thread,
        gmail_message_id="msg_th1",
        subject="Project Alpha Launch",
        date=now - timedelta(hours=3),
    )
    a1 = await seed_analysis(
        test_session,
        m1,
        category="PROJECT",
        urgency_score=4,
        summary="Kickoff discussion on milestones.",
    )
    await seed_action_item(
        test_session,
        a1,
        m1,
        description="Deploy staging cluster",
        deadline=now + timedelta(days=2),
    )

    resp = await client.get(f"/api/v1/threads/{thread.id}/triage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["thread_id"] == str(thread.id)
    assert data["gmail_thread_id"] == "gmail_th_xyz"
    assert data["subject"] == "Project Alpha Launch"
    assert "Alpha launch approved" in data["executive_summary"]
    assert data["max_urgency"] == 4
    assert len(data["message_analyses"]) == 1
    assert len(data["action_items"]) == 1
    assert data["action_items"][0]["description"] == "Deploy staging cluster"


@pytest.mark.asyncio
async def test_thread_triage_endpoint_by_gmail_id(
    client: AsyncClient,
    test_session: AsyncSession,
):
    """GET /api/v1/threads/{thread_id}/triage resolves by Gmail thread ID."""
    account = await seed_account(test_session, email="triage_gmail_id@example.com")
    thread = await seed_thread(
        test_session,
        account,
        gmail_thread_id="189f3a2b1c4d5e6f",
        subject="Gmail Thread ID Lookup",
        summary="Briefing for Gmail thread ID lookup.",
    )
    resp = await client.get("/api/v1/threads/189f3a2b1c4d5e6f/triage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["thread_id"] == str(thread.id)
    assert data["gmail_thread_id"] == "189f3a2b1c4d5e6f"
    assert data["subject"] == "Gmail Thread ID Lookup"


@pytest.mark.asyncio
async def test_thread_triage_not_found(client: AsyncClient):
    """GET /api/v1/threads/{thread_id}/triage returns 404 for unknown thread."""
    non_existent_uuid = str(uuid.uuid4())
    resp = await client.get(f"/api/v1/threads/{non_existent_uuid}/triage")
    assert resp.status_code == 404
    assert "Thread not found" in resp.json()["detail"]
