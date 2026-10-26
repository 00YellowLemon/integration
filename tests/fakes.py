"""Hermetic test doubles for the triage agent: a scripted fake chat model.

Used by the Phase 3 pytest suite instead of live Gemini. The fake returns a
scripted sequence of ``AIMessage`` responses (including tool calls) and a
final content message, so tests can drive the agent loop deterministically
with no network access.

Conventions:
- ``bind_tools`` records the bound tool names (for assertions) and returns
  ``self`` so the same scripted sequence is consumed by the agent loop.
- ``responses`` is a list whose entries are either ``AIMessage`` objects or
  callables ``messages -> AIMessage`` (so a response can depend on input).
- When the sequence is exhausted, a plain ``AIMessage("Done.")`` is returned
  so multi-invoke memory tests do not raise.
- Setting ``raise_exc`` makes the next ``_generate`` raise, simulating a model
  failure (used to exercise decision D8).
"""
from typing import Any, Callable, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict


class ScriptedChatModel(BaseChatModel):
    """A scripted chat model that replays a fixed sequence of AI messages."""

    responses: List[Any] = []
    raise_exc: Optional[Exception] = None
    bound_tool_names: List[str] = []
    call_count: int = 0
    received_messages: List[List[Any]] = []  # per-call message snapshots for assertions

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        # Record bound tool names for assertions; keep state on the same instance.
        self.bound_tool_names = [getattr(t, "name", getattr(t, "name", str(t))) for t in tools]
        return self

    def _next_response(self, messages) -> AIMessage:
        idx = self.call_count
        self.call_count += 1
        self.received_messages.append(list(messages))
        if idx < len(self.responses):
            resp = self.responses[idx]
            if callable(resp):
                resp = resp(messages)
            return resp
        return AIMessage(content="Done.")

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if self.raise_exc is not None:
            exc = self.raise_exc
            self.raise_exc = None  # raise once
            raise exc
        message = self._next_response(messages)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def reset(self) -> None:
        """Reset scripted state so the same instance can be reused across runs."""
        self.call_count = 0
        self.received_messages = []


def tool_call_message(name: str, args: dict, *, call_id: Optional[str] = None) -> AIMessage:
    """Helper to build an AIMessage carrying a single tool call."""
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id or f"call_{name}", "type": "tool_call"}],
    )
