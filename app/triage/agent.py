"""LangChain triage agent assembly on Vertex AI (Gemini 3.8 Flash, ADC).

- ``build_model()`` returns a ``ChatGoogleGenerativeAI`` configured for Vertex
  AI with Application Default Credentials. No API key is ever passed (D3).
- ``build_agent()`` wires the model, the per-run tools, the system prompt, and
  the checkpointer into ``create_agent``.
- ``invoke_triage()`` runs the agent under the thread-scoped memory config.

Tests substitute a scripted fake chat model and ``InMemorySaver`` by calling
``build_agent(db, state, model=fake, checkpointer=memory)``.
"""
import logging
from typing import Any, Optional

from langchain.agents import create_agent
from langchain_core.tools import BaseTool
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.retry import with_retry
from app.triage.checkpointer import get_checkpointer, memory_thread_id

from app.triage.prompts import TRIAGE_SYSTEM_PROMPT
from app.triage.tools import TriageRunState, build_triage_tools

logger = logging.getLogger("gmail_assistant.triage.agent")

RECURSION_LIMIT = 25


class TriageDisabledError(RuntimeError):
    """Raised when triage is disabled or no GCP project can be resolved for Vertex AI."""


def build_model():
    """Return a ``ChatGoogleGenerativeAI`` configured for Vertex AI via ADC.

    Uses the configured ``TRIAGE_MODEL`` and ``GOOGLE_CLOUD_LOCATION`` (default
    ``global``). No ``api_key`` / ``google_api_key`` argument is passed — the
    SDK picks up Application Default Credentials automatically, matching the
    keyless identity model used for Pub/Sub and planned for Cloud Run.

    Raises ``TriageDisabledError`` when triage is disabled or no GCP project
    can be resolved.
    """
    settings = get_settings()
    project = settings.resolve_gcp_project()
    if not settings.triage_ready or project is None:
        raise TriageDisabledError(
            "Triage is disabled (TRIAGE_ENABLED=false) or no GCP project could be "
            "resolved from GCP_PROJECT_ID / ADC. Run `gcloud auth application-default "
            "login` and set GCP_PROJECT_ID."
        )

    from langchain_google_genai import ChatGoogleGenerativeAI

    # The SDK kwarg is `retries` (not `max_retries`); map the triage setting onto it.
    return ChatGoogleGenerativeAI(
        model=settings.TRIAGE_MODEL,
        project=project,
        location=settings.GOOGLE_CLOUD_LOCATION,
        vertexai=True,
        temperature=settings.TRIAGE_TEMPERATURE,
        retries=settings.TRIAGE_MAX_RETRIES,
    )


def build_agent(
    db: AsyncSession,
    state: TriageRunState,
    *,
    model: Any = None,
    checkpointer: Optional[Any] = None,
    tools: Optional[list[BaseTool]] = None,
):
    """Assemble the triage agent.

    Args:
        db: The run's AsyncSession (closed over by the tools).
        state: Per-run ``TriageRunState``.
        model: Optional model override (tests pass a scripted fake chat model).
        checkpointer: Optional checkpointer override (tests pass ``InMemorySaver``).
        tools: Optional pre-built tool list (tests may inject scripted tools).
    """
    if model is None:
        model = build_model()
    if checkpointer is None:
        checkpointer = get_checkpointer()
    if tools is None:
        tools = build_triage_tools(db, state)

    return create_agent(
        model=model,
        tools=tools,
        system_prompt=TRIAGE_SYSTEM_PROMPT,
        checkpointer=checkpointer,
        name="gmail_triage_agent",
    )


@with_retry(max_attempts=3, min_wait=1.0, max_wait=30.0)
async def invoke_triage(

    agent,
    message_id: str,
    thread_id: str,
    instruction: str,
    *,
    recursion_limit: int = RECURSION_LIMIT,
) -> dict:
    """Invoke the agent under the thread-scoped memory config.

    Returns a dict with the final assistant message ``content`` and the count
    of tool-call turns observed in the run.
    """
    from langchain_core.messages import AIMessage

    config = {
        "configurable": {"thread_id": memory_thread_id(thread_id)},
        "recursion_limit": recursion_limit,
    }
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": instruction}]},
        config=config,
    )
    messages = result.get("messages", []) if isinstance(result, dict) else []
    tool_calls = 0
    for m in messages:
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            tool_calls += len(m.tool_calls)
    final_content = ""
    if messages:
        final_content = getattr(messages[-1], "content", "") or ""
    return {"content": final_content, "tool_call_count": tool_calls, "raw": result}
