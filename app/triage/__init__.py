"""LangChain Email Triage Agent package.

This package owns the Phase 3 triage stack:

- ``schemas``: validated Pydantic contracts + ``content_hash``.
- ``checkpointer``: ``AsyncPostgresSaver`` lifecycle (PostgreSQL short-term memory).
- ``tools``: per-run ``@tool`` factory (inspect / classify / extract / synthesise).
- ``agent``: ``create_agent`` assembly, Vertex AI model factory, ``invoke_triage``.
- ``prompts``: rubric-driven system prompt.
- ``threads``: chronological digest builder + change detection for synthesis.
- ``service``: post-ingestion worker, per-message triage, thread synthesis.

The agent is strictly read-only with respect to Gmail: tools read PostgreSQL
only and never instantiate ``GmailClient`` or call any Gmail write method.
"""
