"""Tests for agent assembly, the system prompt, and thread-scoped memory."""
import uuid

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.triage.agent import (
    RECURSION_LIMIT,
    TriageDisabledError,
    build_agent,
    build_model,
    invoke_triage,
)
from app.triage.checkpointer import memory_thread_id
from app.triage.prompts import TRIAGE_SYSTEM_PROMPT
from app.triage.tools import TriageRunState
from tests.fakes import ScriptedChatModel, tool_call_message


def _fake_model_for_classify(message_id: str) -> ScriptedChatModel:
    return ScriptedChatModel(responses=[
        tool_call_message("inspect_email_payload", {"message_id": message_id}),
        tool_call_message(
            "classify_urgency_and_category",
            {
                "message_id": message_id,
                "category": "GENERAL",
                "urgency_score": 2,
                "rationale": "informational",
                "confidence": 0.7,
            },
        ),
        tool_call_message(
            "extract_action_items_and_deadlines",
            {"message_id": message_id, "items": []},
        ),
        AIMessage(content="done"),
    ])


def test_memory_thread_id_convention():
    assert memory_thread_id("th_123") == "gmail-thread:th_123"


def test_system_prompt_enumerates_tool_order_and_readonly_rule():
    assert "inspect_email_payload" in TRIAGE_SYSTEM_PROMPT
    assert "classify_urgency_and_category" in TRIAGE_SYSTEM_PROMPT
    assert "extract_action_items_and_deadlines" in TRIAGE_SYSTEM_PROMPT
    assert "synthesize_thread_summary" in TRIAGE_SYSTEM_PROMPT
    assert "read-only" in TRIAGE_SYSTEM_PROMPT.lower()
    assert "never" in TRIAGE_SYSTEM_PROMPT.lower() and "send" in TRIAGE_SYSTEM_PROMPT.lower()


def test_build_model_raises_when_disabled():
    from app.core.config import get_settings
    settings = get_settings()
    original = settings.TRIAGE_ENABLED
    settings.TRIAGE_ENABLED = False
    try:
        with pytest.raises(TriageDisabledError):
            build_model()
    finally:
        settings.TRIAGE_ENABLED = original


def test_build_model_raises_when_no_project_resolvable(monkeypatch):
    """When no GCP project resolves (no ADC) build_model raises TriageDisabledError."""
    from app.core.config import Settings

    # Patch the method on the class so all instances return None for the project.
    monkeypatch.setattr(Settings, "resolve_gcp_project", lambda self: None)
    with pytest.raises(TriageDisabledError):
        build_model()


@pytest.mark.asyncio
async def test_build_agent_binds_all_four_tools_and_system_prompt(test_session):
    import uuid as _uuid
    state = TriageRunState(message_id=str(_uuid.uuid4()), thread_id="th")
    fake = ScriptedChatModel(responses=[AIMessage(content="hi")])
    agent = build_agent(test_session, state, model=fake, checkpointer=InMemorySaver())

    await agent.ainvoke(
        {"messages": [{"role": "user", "content": "hi"}]},
        config={"configurable": {"thread_id": "test_thread"}},
    )

    assert set(fake.bound_tool_names) == {
        "inspect_email_payload",
        "classify_urgency_and_category",
        "extract_action_items_and_deadlines",
        "synthesize_thread_summary",
    }
    # CompiledStateGraph nodes include the agent's model/tools nodes.
    assert "model" in agent.nodes
    assert "tools" in agent.nodes


@pytest.mark.asyncio
async def test_invoke_triage_returns_content_and_tool_call_count(test_session):
    from tests.helpers import seed_account, seed_message, seed_thread
    account = await seed_account(test_session)
    thread = await seed_thread(test_session, account)
    message = await seed_message(test_session, thread)
    msg_id = str(message.id)
    thread_id = str(thread.id)

    fake = _fake_model_for_classify(msg_id)
    state = TriageRunState(message_id=msg_id, thread_id=thread_id)
    agent = build_agent(test_session, state, model=fake, checkpointer=InMemorySaver())

    result = await invoke_triage(agent, msg_id, thread_id, "triage it")
    assert isinstance(result, dict)
    assert "content" in result and "tool_call_count" in result
    assert result["tool_call_count"] == 3  # inspect, classify, extract


@pytest.mark.asyncio
async def test_memory_preserves_state_within_same_thread_id(test_session):
    """Two invocations on the same thread_id share checkpointed state."""
    from tests.helpers import seed_account, seed_message, seed_thread
    account = await seed_account(test_session)
    thread = await seed_thread(test_session, account)
    message = await seed_message(test_session, thread)
    msg_id = str(message.id)

    fake = _fake_model_for_classify(msg_id)
    state = TriageRunState(message_id=msg_id, thread_id="th_mem")
    agent = build_agent(test_session, state, model=fake, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": memory_thread_id("th_mem")}, "recursion_limit": RECURSION_LIMIT}

    await agent.ainvoke({"messages": [{"role": "user", "content": "turn one"}]}, config=config)
    state_one = await agent.aget_state(config)
    msgs_after_one = len(state_one.values.get("messages", []))

    await agent.ainvoke({"messages": [{"role": "user", "content": "turn two"}]}, config=config)
    state_two = await agent.aget_state(config)
    msgs_after_two = len(state_two.values.get("messages", []))

    # Second turn's checkpoint builds on the first (more messages accumulated).
    assert msgs_after_two > msgs_after_one


@pytest.mark.asyncio
async def test_different_thread_ids_have_separate_state(test_session):
    from tests.helpers import seed_account, seed_message, seed_thread
    account = await seed_account(test_session)
    thread = await seed_thread(test_session, account)
    message = await seed_message(test_session, thread)
    msg_id = str(message.id)

    fake = _fake_model_for_classify(msg_id)
    state = TriageRunState(message_id=msg_id, thread_id="th_x")
    agent = build_agent(test_session, state, model=fake, checkpointer=InMemorySaver())

    cfg_a = {"configurable": {"thread_id": memory_thread_id("th_a")}, "recursion_limit": RECURSION_LIMIT}
    cfg_b = {"configurable": {"thread_id": memory_thread_id("th_b")}, "recursion_limit": RECURSION_LIMIT}

    await agent.ainvoke({"messages": [{"role": "user", "content": "x"}]}, config=cfg_a)
    await agent.ainvoke({"messages": [{"role": "user", "content": "y"}]}, config=cfg_b)

    state_a = await agent.aget_state(cfg_a)
    state_b = await agent.aget_state(cfg_b)
    # Each thread has its own checkpoint history; states are independent.
    assert state_a.values.get("messages") is not None
    assert state_b.values.get("messages") is not None
    # The last user message in thread A is "x"; in thread B is "y".
    last_a_user = next((m for m in reversed(state_a.values["messages"]) if getattr(m, "type", "") == "human"), None)
    last_b_user = next((m for m in reversed(state_b.values["messages"]) if getattr(m, "type", "") == "human"), None)
    assert last_a_user is not None and "x" in str(last_a_user.content)
    assert last_b_user is not None and "y" in str(last_b_user.content)
