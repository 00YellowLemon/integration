# Feature Requirements: LangChain Email Triage Agent & PostgreSQL Short-Term Memory

- **Feature Directory**: `specs/2026-10-23-langchain-triage-agent`
- **Roadmap Reference**: [Phase 3 (3.1 - 3.6)](file:///c:/Users/lemon/integration/specs/roadmap.md#L81-L117)
- **Status**: Approved for implementation (scope, trigger, and merge gate confirmed 2026-10-23)
- **Target Branch**: `feature/phase-3-langchain-triage-agent`
- **Branch Base**: `feature/phase-2-email-ingestion-pipeline` (Phase 2 ingestion code is present in the working tree; it is not merged into `main` yet)
- **Constitution Guidance**: [specs/mission.md](file:///c:/Users/lemon/integration/specs/mission.md) | [specs/tech-stack.md](file:///c:/Users/lemon/integration/specs/tech-stack.md)

---

## 1. Executive Summary & Context

Phases 1 and 2 deliver a connected Gmail account, encrypted OAuth token storage, a sanitized MIME parser, idempotent `threads` / `messages` ingestion, and Pub/Sub-driven delta synchronization. The inbox is now stored, but nothing interprets it.

This feature delivers **Phase 3: LangChain Email Triage Agent & PostgreSQL Short-Term Memory**:

1. A **LangChain tool-calling agent** built with `create_agent`, powered by `ChatGoogleGenerativeAI(model="gemini-3.8-flash", temperature=0.1)` on **Vertex AI**, using the existing `GCP_PROJECT_ID`, the `global` location, and Application Default Credentials from `gcloud auth`.
2. **Four typed tools** that inspect a stored message, classify urgency/category, extract action items and deadlines, and synthesize thread briefings.
3. **PostgreSQL short-term memory** via `AsyncPostgresSaver` from `langgraph-checkpoint-postgres`, keyed by the Gmail thread ID so successive messages in a conversation resume with prior context.
4. **Persistent triage output** in the new `email_analyses` and `action_items` tables, plus `threads.summary` for executive briefings.
5. A **post-ingestion triage worker** that runs automatically after delta sync and on manual sync, with idempotent skip behavior and a manual re-run endpoint.

The agent stays strictly read-only with respect to Gmail: it reads only from PostgreSQL and never calls a Gmail write method.

---

## 2. Scope

### In-Scope (Roadmap 3.1 - 3.6)

- **Dependencies & configuration**: add `langchain`, `langchain-google-genai`, `langgraph`, `langgraph-checkpoint-postgres`, and `psycopg[binary,pool]`; add Vertex AI settings (`GCP_PROJECT_ID` reuse, `GOOGLE_CLOUD_LOCATION=global`, `GOOGLE_GENAI_USE_VERTEXAI=true`) and triage settings to `app/core/config.py` and `.env.example`.
- **Persistence**: new SQLAlchemy models `EmailAnalysis` and `ActionItem`; reuse `Thread.summary`; register models so `init_db()` creates them.
- **Short-term memory**: `AsyncPostgresSaver` lifecycle managed by the FastAPI lifespan, with schema setup through `await saver.setup()`.
- **Triage schemas**: Pydantic v2 models `CategoryEnum`, `ActionItemResult`, `EmailAnalysisResult`, and `ThreadBriefing`.
- **Tools**: `inspect_email_payload`, `classify_urgency_and_category`, `extract_action_items_and_deadlines`, `synthesize_thread_summary`, all validating inputs and persisting typed results.
- **Agent**: `create_agent(model=..., tools=..., system_prompt=..., checkpointer=...)` with a rubric-driven system prompt and read-only guarantees.
- **Worker**: thread-scoped triage after delta sync and manual sync, running in `BackgroundTasks` with an isolated DB session.
- **Thread synthesis**: chronological multi-message digests stored in `threads.summary`.
- **API**: `POST /api/v1/triage/{message_id}` for a manual (optionally forced) re-run of a single message; the worker remains the primary path.
- **Tests and tooling**: hermetic fake-model pytest suite plus `scripts/verify_triage.py` for live verification.

### Out-of-Scope (Deferred)

- **Automatic triage of the initial 50-thread backfill**: `initial_backfill()` does not fan out to the agent (cost and latency control). Stored backlog can be triaged explicitly through the manual endpoint or `scripts/verify_triage.py`.
- **pgvector embeddings and semantic search** (`email_analyses.embedding`): [Phase 4.1](file:///c:/Users/lemon/integration/specs/roadmap.md#L122-L126). The column is not added in this phase.
- **Digest, action-item feed, and thread triage REST endpoints**: [Phase 4.2](file:///c:/Users/lemon/integration/specs/roadmap.md#L128-L135).
- **Exponential backoff around Gmail and Gemini quotas and the formal read-only assertion module**: [Phase 4.3](file:///c:/Users/lemon/integration/specs/roadmap.md#L136-L140). Phase 3 does bounded retries and graceful degradation only.
- **Any Gmail write capability**: sending, drafting, labeling, deleting, or marking spam is permanently out of scope per [mission.md §3](file:///c:/Users/lemon/integration/specs/mission.md).

---

## 3. Locked Decisions

| ID | Decision | Rationale |
|---|---|---|
| **D1** | Deliver the full Phase 3 scope (3.1 - 3.6), including multi-turn synthesis. | Confirmed by the user; a partial agent would leave `threads.summary` unpopulated. |
| **D2** | Triage is triggered **after ingestion in a FastAPI `BackgroundTask`**, using a fresh DB session from `get_session_maker()`. Manual sync and the Pub/Sub webhook both schedule it. | Keeps the webhook fast, survives ingestion commits, and matches the Q2 decision. |
| **D3** | Model is `gemini-3.8-flash` at `temperature=0.1` on **Vertex AI**, configured with the existing `GCP_PROJECT_ID`, `GOOGLE_CLOUD_LOCATION="global"`, and `vertexai=True`. Authentication is ADC from `gcloud auth application-default login`; **no API key** (`GOOGLE_API_KEY`) is used or stored. | Matches [tech-stack.md §3.1](file:///c:/Users/lemon/integration/specs/tech-stack.md) and the [LangChain Gemini docs](https://docs.langchain.com/oss/python/integrations/chat/google_generative_ai#gemini-enterprise-agent-platform-with-credentials); keeps the keyless identity model already used for Pub/Sub and planned for Cloud Run. |
| **D4** | Memory uses `AsyncPostgresSaver`; `thread_id = "gmail-thread:<gmail_thread_id>"`. | Thread-scoped memory is the constitution requirement and lets later messages reuse earlier triage context. |
| **D5** | Idempotency is enforced by a unique `email_analyses.message_id` plus a `content_hash` of the message. An existing analysis with an unchanged hash is skipped; `force=true` re-runs it. | Protects against duplicate Pub/Sub deliveries and repeated manual syncs. |
| **D6** | Tools are **built per run** and close over the run's `AsyncSession`; all writes for one message commit in a single transaction. | Avoids module-level global sessions and guarantees no half-written triage rows. |
| **D7** | Tool inputs are strict Pydantic v2 models; `urgency_score` is constrained to `1..5` and category is an enum. A DB `CHECK` constraint mirrors the urgency range. | Deterministic structured storage per [mission principle 2](file:///c:/Users/lemon/integration/specs/mission.md#L63). |
| **D8** | Gemini failure never corrupts state: the run logs, leaves the message unanalyzed, and returns a retryable status. No partial analyses or action items are committed. | Required for safe retry after rate limits or outages. |
| **D9** | `initial_backfill()` does not auto-triage. Only delta-sync additions and explicit calls are analyzed. | Avoids an unbounded Gemini bill on first connection. |
| **D10** | Hermetic tests use a scripted fake chat model plus `InMemorySaver`; the Postgres checkpointer is exercised in the live validation layer. | SQLite test DB cannot host the `psycopg`-backed checkpointer tables. |

---

## 4. Data Model Additions

### 4.1 `email_analyses` (`app/models/analysis.py`)

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | UUID | Primary Key, default `uuid4` | Analysis identifier |
| `message_id` | UUID | Foreign Key -> `messages.id` (`ON DELETE CASCADE`), **unique**, indexed, not null | One current analysis per message |
| `category` | String(32) | Not null | `CategoryEnum` value |
| `urgency_score` | Integer | Not null, `CHECK 1..5` | Urgency rubric score |
| `summary` | Text | Not null | Agent rationale / concise analysis |
| `confidence` | Float | Not null, `CHECK 0..1` | Model-reported confidence |
| `content_hash` | String(64) | Not null | SHA-256 of subject, sender, date, and body used for skip logic |
| `created_at` | DateTime (UTC) | Not null | Creation timestamp |
| `updated_at` | DateTime (UTC) | Not null, `onupdate` | Last re-analysis timestamp |

### 4.2 `action_items` (`app/models/analysis.py`)

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | UUID | Primary Key, default `uuid4` | Action item identifier |
| `analysis_id` | UUID | Foreign Key -> `email_analyses.id` (`ON DELETE CASCADE`), indexed, not null | Owning analysis |
| `message_id` | UUID | Foreign Key -> `messages.id` (`ON DELETE CASCADE`), indexed, not null | Source message (feed queries in Phase 4) |
| `description` | Text | Not null | Concrete action required |
| `deadline` | DateTime (UTC) | Nullable, indexed | Extracted due date, if any |
| `assignee` | String(255) | Nullable | Person or team responsible |
| `status` | String(16) | Not null, default `"pending"` | `pending` / `done` / `dismissed` |
| `created_at` | DateTime (UTC) | Not null | Creation timestamp |

### 4.3 Reused and library-managed tables

- `threads.summary` (already defined) stores the synthesized executive briefing.
- `PostgresSaver.setup()` owns `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, and `checkpoint_migrations`. These are never created or altered by hand.

---

## 5. Agent & Tool Contracts (`app/triage/`)

| Tool | Input (validated) | DB effect | Return |
|---|---|---|---|
| `inspect_email_payload` | `message_id: str` | Read-only: message, thread context, prior messages, attachment metadata | JSON payload with body text truncated to `TRIAGE_MAX_BODY_CHARS` |
| `classify_urgency_and_category` | `message_id`, `category: CategoryEnum`, `urgency_score: int (1-5)`, `rationale: str`, `confidence: float (0-1)` | Upsert `email_analyses` by `message_id`, including `content_hash` | Confirmation text |
| `extract_action_items_and_deadlines` | `message_id`, `items: list[ActionItemResult]` | Requires an existing analysis; replaces that analysis's `action_items` | Confirmation with item count |
| `synthesize_thread_summary` | `thread_id` (internal UUID or Gmail ID), `summary: str`, `decisions: list[str] = []`, `pending_items: list[str] = []` | Writes composed markdown to `threads.summary` | Confirmation text |

**Ordering guard**: `extract_action_items_and_deadlines` raises a `ToolException` when the message has not been classified, so the model cannot persist orphan action items.

**Run sequencing**: for a thread, the worker iterates messages chronologically and invokes the agent once per unanalyzed message under the same `thread_id`, then requests the synthesis pass. Message count per run is capped by `TRIAGE_MAX_MESSAGES_PER_THREAD` (default 10).

---

## 6. Runtime Flow

```
Delta sync / manual sync commits new messages
        │
        ▼
BackgroundTasks: triage_thread(thread_id)  ── isolated AsyncSession ──▶ AsyncPostgresSaver
        │
        ├─ skip messages whose content_hash is unchanged
        ├─ create_agent invoke (inspect → classify → extract) per message
        ├─ synthesis pass → threads.summary
        └─ single commit; on failure rollback and log a retryable error
```

The webhook still returns `204` immediately; triage never blocks the Pub/Sub acknowledgement.

---

## 7. Configuration Additions

| Setting | Default | Notes |
|---|---|---|
| `GCP_PROJECT_ID` | existing `Optional[str]` | Already used by Pub/Sub; now also selects the Vertex AI project. Auto-discovered from ADC when omitted |
| `GOOGLE_CLOUD_LOCATION` | `global` | Vertex AI location; `global` is the requested endpoint and can be overridden for region-only models |
| `GOOGLE_GENAI_USE_VERTEXAI` | `True` | Explicit backend selection for the `google-genai` SDK |
| `TRIAGE_ENABLED` | `True` | Master switch; when false no worker or agent is constructed |
| `TRIAGE_MODEL` | `gemini-3.8-flash` | Overridable for a cheaper fallback model |
| `TRIAGE_TEMPERATURE` | `0.1` | Determinism per tech-stack; raise toward `1.0` only if the agent loops (Gemini 3 default) |
| `TRIAGE_MAX_BODY_CHARS` | `12000` | Prompt-size guardrail |
| `TRIAGE_MAX_MESSAGES_PER_THREAD` | `10` | Cost guardrail |
| `TRIAGE_MAX_RETRIES` | `2` | Bounded retry per message |

There is **no `GOOGLE_API_KEY` setting**. Credentials come from ADC (`gcloud auth application-default login`) plus an optional quota project (`gcloud auth application-default set-quota-project <PROJECT_ID>`). `.env.example` gains a documented Vertex AI section. `DATABASE_URL` remains the single source of truth; the checkpointer derives a plain `postgresql://` DSN from it (dropping the `+asyncpg` driver).

---

## 8. Dependencies

Add to `requirements.txt` (versions aligned with what is already installed locally):

```
langchain>=1.3.14
langchain-google-genai>=4.3.2
langgraph>=1.2.10
langgraph-checkpoint-postgres>=3.1.0
psycopg[binary,pool]>=3.3.4
```

`google-genai`, `langchain-core`, and `langgraph-checkpoint` arrive transitively and are not pinned separately. `langchain-google-vertexai` is **not** required: `langchain-google-genai` 4.x supersedes `ChatVertexAI` and selects Vertex when `project`/`vertexai=True` is supplied. No API-key or service-account JSON file is added.

---

## 9. Non-Functional Requirements

1. **Read-only invariant**: no Gmail write scopes or methods are introduced; the agent reads PostgreSQL only.
2. **Boot resilience**: missing ADC/quota project, unreachable Postgres checkpointer, or disabled triage must not prevent OAuth, ingestion, or `/health` from working.
3. **Idempotency**: repeated Pub/Sub delivery, repeated sync, and `force=false` re-runs produce no duplicate analyses or action items.
4. **Latency**: a single-message triage run should complete in under 15 seconds p95 on Flash; the worker caps work per thread.
5. **Observability**: structured log lines for `triage.start`, `triage.skip`, `triage.message.done`, `triage.thread.done`, and `triage.error` with thread/message identifiers, never email bodies.
6. **Cost**: one model invocation chain per unanalyzed message plus one synthesis pass per changed thread.

---

## 10. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Background tasks are cut off when Cloud Run throttles CPU after the response ([tech-stack §6.1](file:///c:/Users/lemon/integration/specs/tech-stack.md#L217-L227)). | Accept for Phase 3 local development; Phase 5 hands ingestion + triage off to a Cloud Run job, Cloud Tasks, or an awaited in-request path. Documented in the runbook. |
| Gemini rate limits or outages leave messages unanalyzed. | `D8`: no partial writes, retryable status, idempotent re-run endpoint. Full backoff lands in Phase 4.3. |
| `gemini-3.8-flash` is unavailable in project/`global`, or the ADC principal lacks `roles/aiplatform.user`. | `TRIAGE_MODEL` and `GOOGLE_CLOUD_LOCATION` are configurable; validation records the exact project/location; a 403 maps to a clear retryable error. |
| Vertex ADC quota project is unset, so requests bill to or are rejected by the wrong project. | Document `gcloud auth application-default set-quota-project <PROJECT_ID>` in `.env.example` and the runbook. |
| Sending full bodies to the model inflates tokens. | Truncate to `TRIAGE_MAX_BODY_CHARS`, strip HTML (already sanitized in Phase 2), cap messages per thread. |
| Checkpointer and async SQLAlchemy share one database with different drivers (`psycopg` vs `asyncpg`). | Centralise DSN conversion in one helper; never pass the `+asyncpg` URL to `psycopg`. |
| Concurrent webhook retries double-triage a message. | Unique `message_id`, `content_hash` skip, and a per-thread advisory lock (`pg_try_advisory_lock`) in the worker. |
