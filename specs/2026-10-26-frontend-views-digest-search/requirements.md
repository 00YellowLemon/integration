# Feature Requirements: Frontend Data Views, Digest APIs & Resilience Guardrails

- **Feature Directory**: `specs/2026-10-26-frontend-views-digest-search`
- **Roadmap Reference**: [Phase 4 (4.2 - 4.3)](file:///c:/Users/lemon/integration/specs/roadmap.md#L128-L140) (Phase 4.1 Embeddings deferred per user directive)
- **Status**: Approved with User Scope Confirmation (Semantic Search deferred; pure Triage intelligence focused)
- **Target Branch**: `feature/phase-4-frontend-views-digest-search`
- **Branch Base**: `feature/phase-3-langchain-triage-agent`
- **Constitution Guidance**: [specs/mission.md](file:///c:/Users/lemon/integration/specs/mission.md) | [specs/tech-stack.md](file:///c:/Users/lemon/integration/specs/tech-stack.md)

---

## 1. Executive Summary & Context

Phases 1, 2, and 3 delivered:
1. **Phase 1**: Secure Google OAuth 2.0 flow with strict `gmail.readonly` permissions, AES-GCM encrypted token persistence, and Gmail API client handshake.
2. **Phase 2**: Provisioned managed PostgreSQL storage, MIME parsing/sanitization, idempotent ingestion, and real-time Pub/Sub push synchronization.
3. **Phase 3**: An autonomous LangChain Email Triage Agent powered by Google Gemini 3.8 Flash on Vertex AI (ADC), with `AsyncPostgresSaver` short-term memory, structured tool calling (`classify_urgency_and_category`, `extract_action_items_and_deadlines`, `synthesize_thread_summary`), and background post-ingestion triage.

The incoming emails are now autonomously triaged into PostgreSQL (`email_analyses`, `action_items`, and `threads.summary`).

**Phase 4 focuses directly on inbox triage intelligence, consumption views, and service resilience**:
- **Why Embeddings & Semantic Search are Excluded**: Per user confirmation, the system's core role is an **inbound triage agent** (surfacing today's priorities, urgent emails, extracted deadlines, and thread briefings). Embeddings add latency, API cost, and database complexity for searching historical emails that are not needed for inbox triage. Vector embeddings and semantic search (Roadmap 4.1) are explicitly **deferred**.
- **What Phase 4 Delivers**:
  1. **Frontend-Facing PostgreSQL Views & Indexes**: Optimized database views (`v_urgent_triage_feed`, `v_pending_action_items`, `v_today_digest`) allowing frontend dashboards to connect directly to PostgreSQL via the connection string.
  2. **FastAPI Digest & Triage REST APIs**: Clean, high-speed REST endpoints (`GET /api/v1/digest/today`, `GET /api/v1/action-items`, `GET /api/v1/threads/{thread_id}/triage`).
  3. **Resilience & Rate Limiting**: Exponential backoff with randomized jitter on Google Gmail API (429/503) and Gemini API quotas.
  4. **Strict Read-Only Safety Guardrail**: Code-level verification ensuring zero send/draft/delete capabilities exist in the application.

---

## 2. Scope

### In-Scope (Phase 4 Core Triage)

1. **Frontend-Facing PostgreSQL Views & Indexes (Roadmap 4.2)**:
   - Create SQL views in PostgreSQL:
     - `v_urgent_triage_feed`: Joins `messages`, `email_analyses`, and `threads` where `urgency_score >= 4` or `category IN ('URGENT', 'ACTION_REQUIRED')`.
     - `v_pending_action_items`: Joins `action_items` with source messages, sorted by upcoming deadlines.
     - `v_today_digest`: Aggregates the last 24 hours of incoming emails, showing counts and averages per category.
   - Add optimized indexes on `email_analyses(urgency_score, created_at)` and `action_items(status, deadline)`.
2. **FastAPI Triage & Digest REST APIs (Roadmap 4.2)**:
   - `GET /api/v1/digest/today`: Daily inbox digest summarizing email volume, urgency distribution, and category breakdowns.
   - `GET /api/v1/action-items`: Feed of pending action items with filtering by `status`, `assignee`, and deadline.
   - `GET /api/v1/threads/{thread_id}/triage`: Detailed executive briefing for a thread, including decisions, pending actions, and message triage.
3. **Resilience & Rate Limiting (Roadmap 4.3)**:
   - Exponential backoff with jitter using `tenacity` for Gmail API calls (HTTP 429/503) and Gemini calls (`ResourceExhausted`).
   - Graceful degradation: errors in background triage never corrupt stored emails or crash the sync worker.
4. **Read-Only Safety Guardrail (Roadmap 4.3)**:
   - Automated startup assertion checking active OAuth scopes (`gmail.readonly`).
   - Dynamic introspection of `GmailClient` to guarantee absence of `send`, `draft`, `delete`, or `modify` methods.
5. **Testing & Verification (Roadmap 4.4)**:
   - Hermetic test suite covering all digest endpoints, views, retry logic, and guardrails.
   - Live walkthrough script verifying database views and REST endpoints against live Supabase data.

### Out-of-Scope (Deferred)

- **Vector Embeddings & Semantic Search (Roadmap 4.1)**: Deferred per user directive. No `pgvector` extension or `VECTOR` column is required in this phase.
- **Production Cloud Run Deployments (Procfile, Secret Manager, Cloud Build)**: [Phase 5](file:///c:/Users/lemon/integration/specs/roadmap.md#L147-L201).
- **Outbound Email Mutations**: Sending, drafting, labeling, or deleting emails is permanently prohibited per [mission.md §3](file:///c:/Users/lemon/integration/specs/mission.md#L54-L58).

---

## 3. Locked Decisions

| ID | Decision | Rationale |
|---|---|---|
| **D1** | **Defer Roadmap 4.1 (Vector Embeddings & Semantic Search)**. Focus Phase 4 strictly on 4.2 (Views & Digest APIs) and 4.3 (Resilience & Guardrails). | The project is an email triage agent. Triage focuses on immediate incoming priority, action items, and thread briefings. Stored email search is out of scope for immediate triage operations. |
| **D2** | Frontend views (`v_urgent_triage_feed`, `v_pending_action_items`, `v_today_digest`) are provisioned via `init_db()` in PostgreSQL. | Allows frontend dashboards (Retool, Metabase, Supabase Studio, custom UI) to query PostgreSQL directly via connection string as specified in `tech-stack.md §1 & §2`. |
| **D3** | Composite database indexes added to `email_analyses` and `action_items`. | Guarantees sub-millisecond query performance for urgent feeds and deadline-sorted action items. |
| **D4** | FastAPI endpoints in `app/api/digest.py` return typed Pydantic response models. | Provides typed contracts and automatic OpenAPI documentation for frontend client integration. |
| **D5** | Exponential backoff implemented with `tenacity` covering Gmail and Gemini quotas. | Prevents rate-limit cascading failures and respects Google API quotas cleanly. |
| **D6** | Read-only guardrail enforced at application startup in `lifespan`. | Hardens security guarantees: system will refuse to boot if any mutation capability or non-readonly scope is introduced. |
| **D7** | Dual-layer testing: hermetic SQLite tests for CI, live Supabase script for end-to-end verification. | Ensures quick, reliable local testing while confirming real PostgreSQL view behavior. |

---

## 4. Database Views & Indexes

### 4.1 `v_urgent_triage_feed`
Selects from `email_analyses` joined to `messages` and `threads`:
- Criteria: `urgency_score >= 4 OR category IN ('URGENT', 'ACTION_REQUIRED')`
- Columns: `message_id`, `thread_id`, `subject`, `sender`, `date`, `category`, `urgency_score`, `summary`, `action_items_count`.

### 4.2 `v_pending_action_items`
Selects from `action_items` joined to `messages` and `threads`:
- Criteria: `status = 'pending'`
- Order: `deadline ASC NULLS LAST, created_at DESC`
- Columns: `id`, `description`, `deadline`, `assignee`, `status`, `message_id`, `thread_id`, `subject`.

### 4.3 `v_today_digest`
Aggregates messages and analyses within the last 24 hours:
- Groups by `category` with count, average urgency score, and high-urgency summaries.

### 4.4 Strategic Indexes
- `ix_email_analyses_urgency_created`: `(urgency_score DESC, created_at DESC)`
- `ix_action_items_status_deadline`: `(status, deadline ASC NULLS LAST)`

---

## 5. API Endpoints (`app/api/digest.py`)

- `GET /api/v1/digest/today`: Returns today's summary counts, urgency distribution, and categorized highlights.
- `GET /api/v1/action-items`: Returns pending action items with optional filters (`status`, `has_deadline`, `limit`, `offset`).
- `GET /api/v1/threads/{thread_id}/triage`: Returns executive briefing, decisions, pending actions, and message triage for a thread.

---

## 6. Resilience & Safety Guardrails (`app/core/retry.py`, `app/core/guardrails.py`)

- **Retry Handler**: Async retry decorator with exponential backoff and jitter for HTTP 429/503 and Gemini `ResourceExhausted`.
- **Read-Only Guardrail**: Startup assertion verifying active scopes only include `gmail.readonly` and dynamic introspection of `GmailClient` to confirm zero mutation methods.
