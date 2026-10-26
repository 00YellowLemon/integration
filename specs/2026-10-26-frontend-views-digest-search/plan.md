# Implementation Plan: Frontend Data Views, Digest APIs & Resilience Guardrails

- **Feature Directory**: `specs/2026-10-26-frontend-views-digest-search`
- **Target Branch**: `feature/phase-4-frontend-views-digest-search`
- **Specification Reference**: [requirements.md](file:///c:/Users/lemon/integration/specs/2026-10-26-frontend-views-digest-search/requirements.md)
- **Constitution Guidance**: [specs/mission.md](file:///c:/Users/lemon/integration/specs/mission.md) | [specs/tech-stack.md](file:///c:/Users/lemon/integration/specs/tech-stack.md) | [specs/roadmap.md](file:///c:/Users/lemon/integration/specs/roadmap.md#L128-L145)
- **Validation**: [validation.md](file:///c:/Users/lemon/integration/specs/2026-10-26-frontend-views-digest-search/validation.md)

---

## Task Group 1: Dependencies, Configuration & Database Indexes

- [x] **Task 1.1: Add Resilience Dependency to `requirements.txt`**
  - Add `tenacity>=8.3.0` for async exponential backoff and rate limit handling.
  - Verify `tenacity` imports cleanly in the virtual environment.

- [x] **Task 1.2: Extend Application Settings (`app/core/config.py`)**
  - Add `GMAIL_RATE_LIMIT_RETRIES: int = 4`, `GMAIL_RETRY_BACKOFF_MIN: float = 1.0`, `GMAIL_RETRY_BACKOFF_MAX: float = 30.0`.
  - Add `GEMINI_RATE_LIMIT_RETRIES: int = 3`.
  - Update `.env.example` with documentation for retry and rate-limit guardrail settings.

- [x] **Task 1.3: Create Database Indexes (`app/models/analysis.py`, `app/db/session.py`)**
  - Add composite index on `email_analyses` for `(urgency_score DESC, created_at DESC)`.
  - Add composite index on `action_items` for `(status, deadline ASC NULLS LAST)`.
  - Ensure indexes are registered on model definitions so `Base.metadata.create_all` provisions them automatically.

---

## Task Group 2: Optimized PostgreSQL Views & Direct Database Access

- [x] **Task 2.1: Define PostgreSQL Views (`app/db/views.py`)**
  - Create view `v_urgent_triage_feed`:
    - Joins `email_analyses`, `messages`, and `threads`.
    - Filters: `urgency_score >= 4 OR category IN ('URGENT', 'ACTION_REQUIRED')`.
    - Selects: `message_id`, `thread_id`, `subject`, `sender`, `date`, `category`, `urgency_score`, `summary`, and total action items count.
  - Create view `v_pending_action_items`:
    - Joins `action_items` with `messages` and `threads`.
    - Filters: `status = 'pending'`.
    - Orders by: `deadline ASC NULLS LAST, created_at DESC`.
    - Selects: `id`, `description`, `deadline`, `assignee`, `status`, `message_id`, `thread_id`, `subject`.
  - Create view `v_today_digest`:
    - Aggregates messages and analyses within the last 24 hours.
    - Groups by `category` with count, average urgency score, and high-urgency summaries.

- [x] **Task 2.2: Hook View Creation into Startup Lifecycle (`app/db/session.py`, `app/main.py`)**
  - Implement `create_views(engine: AsyncEngine)` to idempotently execute `CREATE OR REPLACE VIEW ...` on PostgreSQL.
  - Guard view creation so SQLite in-memory test databases do not fail on PostgreSQL-specific dialect features.
  - Call `create_views()` during application `lifespan` startup in `app/main.py`.

---

## Task Group 3: Digest & Triage REST API Endpoints

- [x] **Task 3.1: Author Digest Pydantic Response Schemas (`app/schemas/digest.py` or `app/triage/schemas.py`)**
  - `TodayDigestResponse`: summary counts, urgency score distribution (1 to 5), and categorized highlights.
  - `ActionItemFeedItem`: id, description, deadline, assignee, status, message_id, thread_id, subject.
  - `ActionItemsResponse`: total count, list of `ActionItemFeedItem`.
  - `ThreadTriageResponse`: thread_id, gmail_thread_id, subject, executive_summary, max_urgency, action_items, message_analyses.

- [x] **Task 3.2: Implement Digest Router (`app/api/digest.py`)**
  - Implement `GET /api/v1/digest/today`:
    - Queries today's email triage records grouped by category and urgency.
    - Computes urgency distribution histogram and urgent highlights.
  - Implement `GET /api/v1/action-items`:
    - Supports query parameters: `status` (default `"pending"`), `has_deadline`, `limit`, `offset`.
    - Returns structured list of actionable commitments with deadlines and owning message context.
  - Implement `GET /api/v1/threads/{thread_id}/triage`:
    - Returns complete executive briefing, decisions, pending actions, and message triage for the thread.
    - Supports lookup by either internal UUID or Gmail `thread_id`.

- [x] **Task 3.3: Mount Router & Verify OpenAPI Documentation (`app/main.py`)**
  - Mount `digest_router` under `/api/v1` in `app/main.py`.
  - Verify routes appear in `/docs` Swagger UI with accurate status codes and response schemas.

---

## Task Group 4: Resilience, Rate Limiting & Safety Guardrails

- [x] **Task 4.1: Exponential Backoff & Jitter Handler (`app/core/retry.py`)**
  - Build reusable async retry decorator `with_retry(max_attempts=4, min_wait=1.0, max_wait=30.0)` using `tenacity`.
  - Catch Google API 429/503 quota errors and Vertex AI `ResourceExhausted`.
  - Apply retry decorator to `GmailClient` methods in `app/services/gmail.py` and model invocation in `app/triage/service.py`.

- [x] **Task 4.2: Graceful Degradation & Quota Isolation**
  - When Gemini or Gmail API limits persist past retries, ensure background workers log the error cleanly and exit without corrupting stored emails or failing subsequent jobs.
  - Ensure API endpoints return appropriate 429/503 HTTP responses with retry-after context when available.

- [x] **Task 4.3: Formal Read-Only Safety Guardrail (`app/core/guardrails.py`)**
  - Implement `verify_read_only_safety()`:
    - Verifies that `GOOGLE_CLIENT_SCOPES` only contains `gmail.readonly` and `userinfo.email`.
    - Introspects `GmailClient` to guarantee absence of `send`, `draft`, `delete`, or `modify` methods.
    - Raises `ReadOnlySafetyViolationError` if any mutation capability is detected.
  - Hook assertion into application startup in `lifespan` in `app/main.py`.

---

## Task Group 5: Automated Testing & Verification Suite

- [x] **Task 5.1: Hermetic Unit Tests (CI-Ready)**
  - `tests/test_digest_api.py`: Test `/api/v1/digest/today`, `/api/v1/action-items`, and `/api/v1/threads/{thread_id}/triage` using SQLite test database.
  - `tests/test_guardrails.py`: Test read-only guardrail assertion, scope checking, and retry backoff mechanics.

- [x] **Task 5.2: Live Verification Walkthrough Script (`scripts/verify_phase4.py`)**
  - Connect to live Supabase PostgreSQL and verify database views `v_urgent_triage_feed`, `v_pending_action_items`, `v_today_digest`.
  - Query live REST endpoints `/api/v1/digest/today`, `/api/v1/action-items`, and `/api/v1/threads/{thread_id}/triage`.
  - Validate read-only assertion passes against the active environment.

