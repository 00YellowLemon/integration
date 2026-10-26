# Implementation Plan: LangChain Email Triage Agent & PostgreSQL Short-Term Memory

- **Feature Directory**: `specs/2026-10-23-langchain-triage-agent`
- **Target Branch**: `feature/phase-3-langchain-triage-agent`
- **Specification Reference**: [requirements.md](file:///c:/Users/lemon/integration/specs/2026-10-23-langchain-triage-agent/requirements.md)
- **Constitution Guidance**: [specs/mission.md](file:///c:/Users/lemon/integration/specs/mission.md) | [specs/tech-stack.md](file:///c:/Users/lemon/integration/specs/tech-stack.md) | [specs/roadmap.md](file:///c:/Users/lemon/integration/specs/roadmap.md#L81-L117)
- **Validation**: [validation.md](file:///c:/Users/lemon/integration/specs/2026-10-23-langchain-triage-agent/validation.md)

---

## Task Group 1: Dependencies, Configuration & Credentials

- [x] **Task 1.1: Add Triage Dependencies**
  - Append to `requirements.txt`:
    - `langchain>=1.3.14`
    - `langchain-google-genai>=4.3.2`
    - `langgraph>=1.2.10`
    - `langgraph-checkpoint-postgres>=3.1.0`
    - `psycopg[binary,pool]>=3.3.4`
  - Confirm `create_agent`, `ChatGoogleGenerativeAI`, `AsyncPostgresSaver`, and `InMemorySaver` import cleanly in a fresh venv.

- [x] **Task 1.2: Extend Settings for Gemini on Vertex AI (`app/core/config.py`)**
  - Do **not** add `GOOGLE_API_KEY`: Phase 3 authenticates through Vertex AI with Application Default Credentials (`gcloud auth application-default login`), not an AI Studio key.
  - Reuse the existing `GCP_PROJECT_ID` setting for the Vertex project; it is already auto-discovered from ADC when omitted.
  - Add `GOOGLE_CLOUD_LOCATION: str = "global"` (Vertex global endpoint).
  - Add `GOOGLE_GENAI_USE_VERTEXAI: bool = True` (documented backend-selection flag; keeps process-wide behaviour explicit).
  - Add `TRIAGE_ENABLED: bool = True`, `TRIAGE_MODEL: str = "gemini-3.8-flash"`, `TRIAGE_TEMPERATURE: float = 0.1`.
  - Add `TRIAGE_MAX_BODY_CHARS: int = 12000`, `TRIAGE_MAX_MESSAGES_PER_THREAD: int = 10`, `TRIAGE_MAX_RETRIES: int = 2`.
  - Add `resolve_gcp_project()` helper that returns `GCP_PROJECT_ID` or falls back to `google.auth.default()` discovery, and a `triage_ready` property returning `TRIAGE_ENABLED and resolve_gcp_project() is not None`. No key check, so the app still boots without GCP access.

- [x] **Task 1.3: DSN Conversion Helper (`app/triage/checkpointer.py` or `app/db/dsn.py`)**
  - `to_psycopg_dsn(database_url: str) -> str`: strip `+asyncpg` / `+psycopg`, normalise `postgres://` to `postgresql://`.
  - Reject non-PostgreSQL URLs with a clear error; the checkpointer is never constructed for SQLite tests.

- [x] **Task 1.4: Update `.env.example`**
  - Add a "Gemini via Vertex AI (ADC)" section documenting `GCP_PROJECT_ID`, `GOOGLE_CLOUD_LOCATION=global`, `GOOGLE_GENAI_USE_VERTEXAI=true`, `TRIAGE_ENABLED`, `TRIAGE_MODEL`, and the guardrail settings.
  - State explicitly that **no API key is used** and that credentials come from `gcloud auth application-default login`; do not add a `GOOGLE_API_KEY` placeholder.
  - Mention the quota project requirement: `gcloud auth application-default set-quota-project <PROJECT_ID>`.

- [x] **Task 1.5: Document & Verify GCP Prerequisites (one-time, manual)**
  - Enable the Vertex AI API: `gcloud services enable aiplatform.googleapis.com --project <PROJECT_ID>`.
  - Grant the authenticated principal the Vertex user role: `gcloud projects add-iam-policy-binding <PROJECT_ID> --member="user:<you>@example.com" --role="roles/aiplatform.user"`.
  - Authenticate ADC with the project and quota project:
    ```powershell
    gcloud auth application-default login
    gcloud auth application-default set-quota-project <PROJECT_ID>
    ```
  - Confirm the active project with `gcloud config get-value project`; this value should match `GCP_PROJECT_ID`.
  - Record the exact project and `global` location in the merge PR so the live test is reproducible.
  - No `langchain-google-vertexai` / `ChatVertexAI` is added: `langchain-google-genai` 4.x supersedes it and selects Vertex when `project`/`vertexai=True` is provided.

---

## Task Group 2: Persistence Models & Triage Schemas

- [x] **Task 2.1: Implement `EmailAnalysis` Model (`app/models/analysis.py`)**
  - Columns per [requirements.md §4.1](file:///c:/Users/lemon/integration/specs/2026-10-23-langchain-triage-agent/requirements.md):
    - `id` (GUID PK), `message_id` (GUID FK `messages.id` `ON DELETE CASCADE`, unique, indexed, not null)
    - `category` (String(32), not null), `urgency_score` (Integer, `CheckConstraint("urgency_score BETWEEN 1 AND 5")`)
    - `summary` (Text, not null), `confidence` (Float, `CheckConstraint("confidence >= 0 AND confidence <= 1")`)
    - `content_hash` (String(64), not null), `created_at`, `updated_at`
  - Relationships: `message` (back_populates `analysis`), `action_items` (one-to-many, cascade `all, delete-orphan`).

- [x] **Task 2.2: Implement `ActionItem` Model (`app/models/analysis.py`)**
  - Columns per [requirements.md §4.2](file:///c:/Users/lemon/integration/specs/2026-10-23-langchain-triage-agent/requirements.md), both FKs `ON DELETE CASCADE`.
  - `status` defaults to `"pending"`; index on `deadline` and `status` for the Phase 4 feed.

- [x] **Task 2.3: Wire Models into Metadata**
  - Add `analysis: Mapped[Optional["EmailAnalysis"]]` to `app/models/message.py` (uselist=False).
  - Export both models from `app/models/__init__.py`.
  - Import them in `app/db/session.py` so `Base.metadata.create_all` provisions the tables on startup.
  - Do **not** add an `embedding` column (Phase 4.1 owns it).

- [x] **Task 2.4: Author Pydantic Triage Schemas (`app/triage/schemas.py`)**
  - `CategoryEnum(str, Enum)`: `URGENT`, `ACTION_REQUIRED`, `INVOICES_RECEIPTS`, `SCHEDULING`, `UPDATES_NEWSLETTERS`, `GENERAL`.
  - `ActionItemResult`: `description` (min length 3), `deadline: Optional[datetime]`, `assignee: Optional[str]`.
  - `ClassificationResult`: `message_id`, `category`, `urgency_score: int = Field(ge=1, le=5)`, `rationale: str`, `confidence: float = Field(ge=0, le=1)`.
  - `EmailAnalysisResult`: classification plus `action_items: list[ActionItemResult]`.
  - `ThreadBriefing`: `summary`, `decisions: list[str]`, `pending_items: list[str]`, `participants: list[str]`.
  - Add `content_hash(subject, sender, date, body_text) -> str` (SHA-256 hex) used by both worker and tool.

---

## Task Group 3: PostgreSQL Short-Term Memory Checkpointer

- [x] **Task 3.1: Checkpointer Lifecycle Manager (`app/triage/checkpointer.py`)**
  - Expose `get_checkpointer() -> AsyncPostgresSaver | None` and `async def init_checkpointer()` / `async def close_checkpointer()`.
  - Implementation: `AsyncExitStack` owning `AsyncPostgresSaver.from_conn_string(to_psycopg_dsn(settings.DATABASE_URL))`, opened with `autocommit=True`, `prepare_threshold=0`, `row_factory=dict_row`.
  - Return `None` when `triage_ready` is false or the URL is not PostgreSQL; log a warning once instead of raising.
  - Idempotent init (safe to call twice); no module-level connection creation at import time.

- [x] **Task 3.2: Run `saver.setup()` and Add Lifespan Hooks (`app/main.py`)**
  - After `init_db()`, call `await init_checkpointer()`, then `await saver.setup()` inside a try/except that logs and continues on failure.
  - On shutdown, dispose the checkpointer before the SQLAlchemy engine.
  - Confirm startup creates `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations` without touching application tables.

- [x] **Task 3.3: Memory Naming Convention**
  - Add `memory_thread_id(gmail_thread_id: str) -> str` returning `f"gmail-thread:{gmail_thread_id}"`.
  - All invocations pass `config={"configurable": {"thread_id": memory_thread_id(...)}, "recursion_limit": 25}`.
  - Document the convention in the module docstring so Phase 4 and re-runs reuse the same history.

---

## Task Group 4: Triage Tools (`app/triage/tools.py`)

- [x] **Task 4.1: Per-Run Tool Factory**
  - `build_triage_tools(db: AsyncSession, state: TriageRunState) -> list[BaseTool]`.
  - `TriageRunState` is a small dataclass holding `message_id`, `thread_id`, `content_hash`, and the last classification (for ordering guards).
  - Tools must close over the session; no global session, no implicit commits inside individual tools.

- [x] **Task 4.2: `inspect_email_payload(message_id: str)`**
  - Load message + thread + prior messages from PostgreSQL (not Gmail).
  - Return a compact dict: `message_id`, `thread_id`, `subject`, `sender`, `recipient`, `date` (ISO), `body_text` truncated to `TRIAGE_MAX_BODY_CHARS`, `attachment_names`, and up to 3 prior message summaries/snippets for context.
  - Raise `ToolException` with a non-sensitive message when the ID is unknown.

- [x] **Task 4.3: `classify_urgency_and_category(message_id, category, urgency_score, rationale, confidence)`**
  - Validate through `ClassificationResult` (enum coercion, 1-5, 0-1).
  - Upsert `email_analyses` by `message_id`: insert a new row or update the existing one and refresh `content_hash` and `updated_at`.
  - Replace nothing else; record `state.last_classification` and return a one-line confirmation.

- [x] **Task 4.4: `extract_action_items_and_deadlines(message_id, items: list[ActionItemResult])`**
  - Require a committed-or-pending analysis for the message; otherwise raise `ToolException("classify first")`.
  - Delete existing `action_items` for that analysis and insert the supplied list (replace semantics).
  - Normalise naive deadlines to UTC; reject deadlines before the message date only with a warning log, not an exception.
  - Return a confirmation containing the item count.

- [x] **Task 4.5: `synthesize_thread_summary(thread_id, summary, decisions=[], pending_items=[])`**
  - Accept either the internal UUID or the Gmail thread ID; resolve to the `threads` row.
  - Compose markdown: `## Summary`, `## Decisions`, `## Pending` and write to `threads.summary`.
  - Reject empty summaries and cap stored text at 8000 characters.

- [x] **Task 4.6: Tool Docstrings & Read-Only Guard**
  - Docstrings must state inputs, side effects, and that tools never call Gmail.
  - Add a module-level assertion/comment inventory confirming no send/draft/delete/modify calls exist.

---

## Task Group 5: Agent Assembly & System Prompt

- [x] **Task 5.1: Model Factory on Vertex AI (`app/triage/agent.py`)**
  - `build_model()` returns a `ChatGoogleGenerativeAI` configured for Vertex AI with ADC:
    ```python
    ChatGoogleGenerativeAI(
        model=settings.TRIAGE_MODEL,                 # "gemini-3.8-flash"
        project=resolve_gcp_project(),               # existing GCP_PROJECT_ID
        location=settings.GOOGLE_CLOUD_LOCATION,     # "global"
        vertexai=True,
        temperature=settings.TRIAGE_TEMPERATURE,     # 0.1 per tech-stack
        max_retries=settings.TRIAGE_MAX_RETRIES,
    )
    ```
  - Do not pass `api_key` / `google_api_key`; the SDK picks up ADC credentials automatically.
  - Do not build a `genai.Client` or service-account file by hand — ADC keeps the runtime keyless and matches the Cloud Run identity planned in Phase 5.
  - Raise a typed `TriageDisabledError` when `triage_ready` is false (triage disabled or no project resolvable).
  - Note in the docstring that `location="global"` is the Vertex global endpoint; it can be overridden via `GOOGLE_CLOUD_LOCATION` if a model is region-only.
  - Gemini 3 defaults to `temperature=1.0` and the docs warn that mid-range values can cause tool-call loops. Keep `0.1` per [tech-stack.md §3.1](file:///c:/Users/lemon/integration/specs/tech-stack.md), but surface `TRIAGE_TEMPERATURE` as the escape hatch if the agent loops in validation.

- [x] **Task 5.2: Author the System Prompt (`app/triage/prompts.py`)**
  - Persona: executive inbox analyst (see [mission.md §2](file:///c:/Users/lemon/integration/specs/mission.md)).
  - Category definitions with examples; urgency rubric 1 (ignore) to 5 (respond today).
  - Mandatory sequence per message: `inspect_email_payload` → `classify_urgency_and_category` → `extract_action_items_and_deadlines`.
  - Thread mode instruction: after the final message, call `synthesize_thread_summary` with decisions and pending items.
  - Hard rules: no speculation beyond the payload, no Gmail writes, no invented deadlines (use `null`), concise output, never echo full email bodies.

- [x] **Task 5.3: Assemble the Agent**
  - `build_agent(db, state)` returns `create_agent(model=build_model(), tools=build_triage_tools(db, state), system_prompt=TRIAGE_SYSTEM_PROMPT, checkpointer=get_checkpointer(), name="gmail_triage_agent")`.
  - Expose `async def invoke_triage(agent, message_id, thread_id, instruction) -> dict` wrapping `ainvoke` with the thread config and returning the final message content plus tool-call trace count.

---

## Task Group 6: Triage Worker, Ingestion Wiring & Manual API

- [x] **Task 6.1: Extend `SyncStats` with Affected Threads (`app/services/ingestion.py`)**
  - Add `thread_ids: list[uuid.UUID]` (internal IDs) with a default of `[]`.
  - Populate it from `ingest_thread` results in both `initial_backfill` and `delta_sync` so the caller knows what to triage.
  - Keep `initial_backfill` free of triage calls (decision D9).

- [x] **Task 6.2: Implement `TriageService` (`app/triage/service.py`)**
  - `async def triage_message(db, message, *, force=False) -> TriageOutcome`: compute `content_hash`, skip when an analysis with the same hash exists, run the agent chain, commit once, and translate failures into a retryable `TriageOutcome(status="error", retryable=True)`.
  - `async def triage_thread(thread_id, *, force=False) -> TriageRunSummary`: open a session via `get_session_maker()`, acquire `pg_try_advisory_lock` on the thread (skip when unavailable), load messages chronologically, triage each unanalyzed message under one memory thread, then run one synthesis pass when anything changed.
  - `async def triage_after_sync(thread_ids: list[uuid.UUID]) -> None`: background entry point with per-thread try/except and structured logging.
  - All logging uses identifiers only; never log bodies or prompts.

- [x] **Task 6.3: Schedule Triage from Ingestion Paths**
  - `app/api/webhook.py`: after `run_background_sync` completes, pass `stats.thread_ids` to `TriageService.triage_after_sync` inside the same background task.
  - `app/api/gmail.py` `POST /api/v1/gmail/sync`: accept `BackgroundTasks` and schedule `triage_after_sync(stats.thread_ids)`; return the sync response immediately.
  - Both paths remain no-ops when `TRIAGE_ENABLED` is false.

- [x] **Task 6.4: Manual Triage Endpoint (`app/api/triage.py`)**
  - `POST /api/v1/triage/{message_id}` with body `{"force": bool = false}`.
  - Resolve the message, run `triage_message` for it and its thread synthesis, return `TriageOutcome` (`status`, `category`, `urgency_score`, `action_items_count`, `skipped`, `error`).
  - Map errors: unknown message → 404; triage disabled → 503; model failure → 502 with a retry hint.
  - Mount the router in `app/main.py`.

- [x] **Task 6.5: Response Schemas**
  - Add `TriageRequest`, `TriageOutcomeResponse`, and `TriageRunSummary` to `app/schemas/triage.py` with `model_config = ConfigDict(from_attributes=True)`.

---

## Task Group 7: Multi-Turn Thread Synthesis & CLI Verification

- [x] **Task 7.1: Chronological Digest Builder (`app/triage/threads.py`)**
  - `build_thread_digest(thread, messages) -> str`: ordered transcript with sender, date, subject, and truncated body, capped by `TRIAGE_MAX_BODY_CHARS` in total.
  - `changed_since_last_analysis(thread, messages) -> bool` compares message hashes with stored analyses to avoid redundant synthesis.

- [x] **Task 7.2: Synthesis Pass**
  - After per-message triage, invoke the agent once with the digest and instruction to call `synthesize_thread_summary`.
  - Store decisions and pending items inside the markdown body (no new columns).
  - Skip synthesis when nothing changed and `force=False`.

- [x] **Task 7.3: Interactive Verification Script (`scripts/verify_triage.py`)**
  - Mirrors `scripts/verify_ingestion.py`: verifies DB connectivity, lists connected accounts, shows untriaged message counts, then triages a chosen thread/message with live Gemini.
  - Prints category, urgency, confidence, action items, and the thread briefing.
  - Supports `--force` and `--message-id` / `--thread-id` flags; exits non-zero on failure.

---

## Task Group 8: Automated Test Suite & Merge Readiness

- [x] **Task 8.1: Scripted Fake Chat Model (`tests/fakes.py`)**
  - Subclass `BaseChatModel` with `bind_tools`, returning scripted `AIMessage(tool_calls=[...])` sequences and a final content message.
  - Supports assertions on bound tool names and received messages.

- [x] **Task 8.2: Schema & Tool Tests (`tests/test_triage_tools.py`)**
  - Reject `urgency_score` 0 and 6, out-of-range confidence, and invalid categories.
  - `inspect_email_payload` truncates bodies and raises for unknown messages.
  - `classify` inserts then updates the same `email_analyses` row.
  - `extract_action_items` refuses to run before classification and replaces items on re-run.
  - `synthesize_thread_summary` composes the expected markdown sections.

- [x] **Task 8.3: Worker & Idempotency Tests (`tests/test_triage_service.py`)**
  - Unchanged `content_hash` → `skipped=True`, zero model calls.
  - `force=True` re-runs and updates the existing row.
  - Simulated model exception → rollback, zero analyses, retryable outcome.
  - Directive D9: `initial_backfill` does not enqueue triage.
  - Advisory-lock contention returns a skipped summary instead of raising.

- [x] **Task 8.4: Agent Wiring & Memory Tests (`tests/test_triage_agent.py`)**
  - `create_agent` receives all four tools and the system prompt.
  - With `InMemorySaver`, two invocations on `gmail-thread:<id>` preserve checkpointed state.
  - `TriageDisabledError` when `TRIAGE_ENABLED=false` or no GCP project can be resolved (no ADC).

- [x] **Task 8.5: API & Ingestion Wiring Tests (`tests/test_triage_api.py`)**
  - `POST /api/v1/triage/{id}` returns 200 with the outcome, 404 for unknown IDs, 503 when disabled.
  - Sync endpoint schedules triage only when messages were ingested.
  - Existing suite (`tests/`) still passes unchanged.

- [x] **Task 8.6: Documentation & Roadmap Tick**
  - Add the triage architecture flow and env vars to the README/runbook section touched in Phase 2.
  - Tick Phase 3 boxes in `specs/roadmap.md` only after [validation.md](file:///c:/Users/lemon/integration/specs/2026-10-23-langchain-triage-agent/validation.md) passes end to end.
