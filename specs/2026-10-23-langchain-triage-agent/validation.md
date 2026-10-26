# Validation & Testing Guide: LangChain Email Triage Agent & PostgreSQL Short-Term Memory

- **Feature Directory**: `specs/2026-10-23-langchain-triage-agent`
- **Target Branch**: `feature/phase-3-langchain-triage-agent`
- **Specification Reference**: [requirements.md](file:///c:/Users/lemon/integration/specs/2026-10-23-langchain-triage-agent/requirements.md) | [plan.md](file:///c:/Users/lemon/integration/specs/2026-10-23-langchain-triage-agent/plan.md)
- **Constitution Reference**: [specs/mission.md](file:///c:/Users/lemon/integration/specs/mission.md) | [specs/tech-stack.md](file:///c:/Users/lemon/integration/specs/tech-stack.md) | [specs/roadmap.md](file:///c:/Users/lemon/integration/specs/roadmap.md#L81-L117)
- **Testing Approach**: Dual-Layer Verification (hermetic fake-model pytest suite + live Gemini/Supabase walkthrough), per the confirmed merge gate.

---

## 1. Prerequisites

- [ ] Python 3.11+ environment with dependencies installed:
  ```powershell
  .venv\Scripts\Activate.ps1
  pip install -r requirements.txt
  ```
- [ ] **Vertex AI access via ADC** (no API key). One-time setup:
  ```powershell
  gcloud config set project <PROJECT_ID>
  gcloud services enable aiplatform.googleapis.com --project <PROJECT_ID>
  gcloud auth application-default login
  gcloud auth application-default set-quota-project <PROJECT_ID>
  ```
  The authenticated principal needs `roles/aiplatform.user` on `<PROJECT_ID>`.
- [ ] `.env` points at the same project and the global Vertex endpoint:
  ```ini
  GCP_PROJECT_ID=<PROJECT_ID>
  GOOGLE_CLOUD_LOCATION=global
  GOOGLE_GENAI_USE_VERTEXAI=true
  TRIAGE_ENABLED=true
  TRIAGE_MODEL=gemini-3.8-flash
  ```
  Do **not** set `GOOGLE_API_KEY`; if it is present, remove it so the SDK cannot silently fall back to the Developer API.
- [ ] `DATABASE_URL` points at the Supabase PostgreSQL instance used in Phase 2.
- [ ] An active account exists in `accounts` and `threads` / `messages` contain ingested mail (Phase 2 validation complete).
- [ ] Import and Vertex-instantiation smoke check succeeds (live call, small token spend):
  ```powershell
  python -c "from langchain.agents import create_agent; from langchain_google_genai import ChatGoogleGenerativeAI; from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver; m = ChatGoogleGenerativeAI(model='gemini-3.8-flash', project='<PROJECT_ID>', location='global', vertexai=True, temperature=0.1); print(m.invoke('Reply with ok').text)"
  ```
  - [ ] Prints `ok` without an authentication error, proving ADC + project + global endpoint are wired.

---

## 2. Layer 1: Hermetic Pytest Suite (CI-Ready)

No network access, no live Gemini, no Supabase. Uses the in-memory SQLite fixtures from `tests/conftest.py`, the scripted fake chat model in `tests/fakes.py`, and `InMemorySaver` instead of `AsyncPostgresSaver`.

### Execution

```powershell
pytest tests/ -v
```

### 2.1 Schema Validation (`tests/test_triage_tools.py`)

- [x] `CategoryEnum` rejects an unknown label and accepts all six defined categories.
- [x] `urgency_score` of `0` and `6` raise `ValidationError`; `1` and `5` pass.
- [x] `confidence` outside `0..1` raises `ValidationError`.
- [x] `ActionItemResult` rejects an empty description and accepts a null deadline/assignee.
- [x] `content_hash()` is stable across re-serialisation and changes when the body changes.

### 2.2 Tool Behaviour (`tests/test_triage_tools.py`)

- [x] `inspect_email_payload` returns subject, sender, date, prior-message context, and a body no longer than `TRIAGE_MAX_BODY_CHARS`.
- [x] `inspect_email_payload` raises `ToolException` for an unknown message ID.
- [x] `classify_urgency_and_category` inserts one `email_analyses` row and updates that same row on a second call (row count stays 1).
- [x] `extract_action_items_and_deadlines` raises before classification and replaces items after a second call (no duplicates).
- [x] `synthesize_thread_summary` writes markdown containing `## Summary`, `## Decisions`, and `## Pending` to `threads.summary`.
- [x] Naive deadlines are persisted as timezone-aware UTC values.

### 2.3 Worker & Idempotency (`tests/test_triage_service.py`)

- [x] First run on a message creates one analysis, the expected action items, and calls the fake model exactly once.
- [x] Second run with the same `content_hash` returns `skipped=True` and makes zero model calls.
- [x] `force=True` on an unchanged message re-runs, updates `updated_at`, and still leaves one row.
- [x] A fake model that raises produces `status="error"` with `retryable=True`, zero committed analyses, and zero committed action items.
- [x] `initial_backfill()` returns `thread_ids` but never invokes the triage service (decision D9).
- [x] Concurrent `triage_thread` calls where the advisory lock is held return a skipped summary without raising.

### 2.4 Agent Wiring & Memory (`tests/test_triage_agent.py`)

- [x] `build_agent` binds all four tools and the system prompt (assert on `agent.nodes` / bound tool names).
- [x] With `InMemorySaver`, two `invoke_triage` calls on `gmail-thread:<id>` resume the same checkpoint; a different thread ID starts a fresh history.
- [x] `build_model` raises `TriageDisabledError` when `TRIAGE_ENABLED=false` or no GCP project can be resolved; when enabled it passes `project`, `location`, and `vertexai=True` with no API key argument.
- [x] The system prompt enumerates the mandatory tool order and the read-only rule.

### 2.5 API & Ingestion Wiring (`tests/test_triage_api.py`)

- [x] `POST /api/v1/triage/{message_id}` returns 200 with `status`, `category`, `urgency_score`, `action_items_count`, and `skipped`.
- [x] Unknown message ID returns 404; disabled triage returns 503; model failure returns 502.
- [x] `POST /api/v1/gmail/sync` schedules `triage_after_sync` only when `thread_ids` is non-empty.
- [x] The pre-existing Phase 1/2 tests (`test_auth`, `test_config`, `test_gmail`, `test_ingestion`, `test_mime_parser`, `test_webhook`) still pass with no modifications.

### 2.6 Suite Gate

- [x] Full run is green:
  ```powershell
  pytest tests/ -v
  # Expected: all tests pass, 0 failures, 0 errors
  ```

---

## 3. Layer 2: Live Gemini + Supabase Walkthrough

### Step 3.1: Startup & Checkpointer Tables

1. Start the API:
   ```powershell
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
   ```
2. Startup logs must show `Database initialized successfully` and a checkpointer init line (or an explicit warning if disabled, never a crash).
3. In the Supabase SQL editor:
   ```sql
   SELECT table_name FROM information_schema.tables
   WHERE table_schema = 'public'
     AND table_name IN ('email_analyses','action_items','checkpoints','checkpoint_blobs','checkpoint_writes','checkpoint_migrations')
   ORDER BY table_name;
   ```
   - [ ] All six tables are present.
4. Confirm the startup guard (triage configuration must not break boot):
   ```powershell
   curl http://127.0.0.1:8000/health
   ```
   - [ ] Returns `{"status":"ok","database":"connected"}`.

### Step 3.2: Manual Sync → Background Triage

1. Send a fresh email to the connected Gmail account from another address.
2. Trigger sync:
   ```powershell
   curl -X POST http://127.0.0.1:8000/api/v1/gmail/sync -H "Content-Type: application/json" -d '{"max_threads": 50}'
   ```
   - [ ] HTTP 200 with `messages_synced >= 1`.
3. Watch the server logs for the ordered chain:
   ```
   triage.start thread_id=...
   triage.message.done message_id=... category=ACTION_REQUIRED urgency=4
   triage.thread.done thread_id=... messages=1 action_items=2
   ```
   - [ ] No email body, prompt text, or ADC token appears in the logs.

### Step 3.3: Database Verification

```sql
SELECT m.subject, a.category, a.urgency_score, a.confidence, a.summary
FROM messages m JOIN email_analyses a ON a.message_id = m.id
ORDER BY a.updated_at DESC LIMIT 5;

SELECT ai.description, ai.deadline, ai.assignee, ai.status
FROM action_items ai ORDER BY ai.created_at DESC LIMIT 10;

SELECT subject, left(summary, 200) FROM threads WHERE summary IS NOT NULL ORDER BY updated_at DESC LIMIT 3;
```

- [ ] The new message has exactly one `email_analyses` row.
- [ ] `category` is one of the six defined values and `urgency_score` is within `1..5`.
- [ ] Extracted action items match the email content; missing deadlines are `NULL` rather than invented.
- [ ] The thread row has a non-null executive briefing with decisions and pending items.

### Step 3.4: Idempotency

1. Re-run the same sync (Pub/Sub retry equivalent).
2. Re-run triage for the same message:
   ```powershell
   curl -X POST http://127.0.0.1:8000/api/v1/triage/<MESSAGE_ID> -H "Content-Type: application/json" -d '{"force": false}'
   ```
   - [ ] Response reports `"skipped": true`.
   - [ ] `SELECT COUNT(*) FROM email_analyses WHERE message_id = '<MESSAGE_ID>';` still returns `1`.
3. Forced re-run:
   ```powershell
   curl -X POST http://127.0.0.1:8000/api/v1/triage/<MESSAGE_ID> -H "Content-Type: application/json" -d '{"force": true}'
   ```
   - [ ] Returns `skipped: false`, the original row's `updated_at` advances, no duplicate rows or action items are created.

### Step 3.5: Short-Term Memory Resumption

1. Choose a thread with at least three messages and triage it through `scripts/verify_triage.py --thread-id <GMAIL_THREAD_ID>`.
2. Verify checkpoint growth:
   ```sql
   SELECT thread_id, COUNT(*) AS checkpoints
   FROM checkpoints
   WHERE thread_id = 'gmail-thread:<GMAIL_THREAD_ID>'
   GROUP BY thread_id;
   ```
   - [ ] Checkpoint count increases across message turns and no checkpoints exist under a per-message key.
3. Invoke the same thread a second time via the script; the agent resumes without losing prior context (no duplicated classification side effects).

### Step 3.6: Live CLI Verification

```powershell
python scripts/verify_triage.py --thread-id <GMAIL_THREAD_ID>
python scripts/verify_triage.py --message-id <GMAIL_MESSAGE_ID> --force
```

- [ ] Script prints account, untriaged counts, category, urgency, confidence, action items, and the thread briefing.
- [ ] Exit code is `0`; using a bogus ID exits non-zero with a clear message.

### Step 3.7: Failure & Degradation

- [ ] Break ADC for Vertex without touching the app config, then re-run a triage call (e.g. `gcloud auth application-default revoke`, or point `GCP_PROJECT_ID` at a project where the principal lacks `roles/aiplatform.user`):
  - Response is 502 with a retry hint (or 503 if triage is disabled); **zero** new `email_analyses` / `action_items` rows are committed.
- [ ] Set `TRIAGE_ENABLED=false`, restart:
  - `/health` still returns `ok`; ingestion and sync still function; triage endpoint returns 503; no checkpointer tables are required for boot.
- [ ] Temporarily block outbound Gemini traffic (or use an unreachable proxy) and confirm the background worker logs `triage.error` without aborting the sync response.

### Step 3.8: Read-Only Audit

```powershell
rg -n "messages\.send|drafts\.create|messages\.delete|messages\.modify|users\.settings" app/
rg -n "gmail\.readonly|gmail\.send|gmail\.modify" app/
```

- [ ] Zero Gmail write calls in `app/`.
- [ ] Only `gmail.readonly` and `userinfo.email` scopes are requested.
- [ ] Triage tools read PostgreSQL only and never instantiate `GmailClient`.

---

## 4. Milestone 3 Test

Ingest a new email thread, let the post-ingestion worker run, and verify:

- [ ] At least one new `messages` row from delta sync.
- [ ] One `email_analyses` row per analyzed message with a valid category and 1-5 urgency score.
- [ ] Action items persisted with deadlines and assignees where the email states them.
- [ ] A synthesized briefing in `threads.summary` for the multi-message thread.
- [ ] `checkpoints` shows state under `gmail-thread:<GMAIL_THREAD_ID>`.
- [ ] Re-running triage is a no-op unless forced.
- [ ] Logs are identifier-only and contain no email content.

---

## 5. Merge Readiness Checklist

Before merging `feature/phase-3-langchain-triage-agent` into `main`:

- [ ] `pytest tests/ -v` is fully green (Phase 1/2 suites included).
- [ ] `email_analyses` and `action_items` exist in Supabase with the documented constraints and cascades.
- [ ] `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, and `checkpoint_migrations` are created by `PostgresSaver.setup()`, not by `Base.metadata`.
- [ ] Live Vertex AI smoke test succeeds against `gemini-3.8-flash` (or the configured `TRIAGE_MODEL`) using `GCP_PROJECT_ID` + `GOOGLE_CLOUD_LOCATION=global` and ADC; the project, location, and model are recorded in the PR.
- [ ] No `GOOGLE_API_KEY`, service-account JSON, or other long-lived Gemini secret exists in `.env`, `.env.example`, or `requirements.txt`; credentials are ADC-only.
- [ ] Idempotency proven: repeated sync and unforced re-runs create no duplicate analyses or items.
- [ ] Multi-turn memory proven: checkpoints are keyed by `gmail-thread:<GMAIL_THREAD_ID>` and resume across runs.
- [ ] `initial_backfill` does not auto-triage (no unexpected Gemini spend on first connection).
- [ ] Gemini failure leaves no partial writes and logs a retryable error.
- [ ] Read-only audit passes; no send/draft/delete/modify methods or scopes exist.
- [ ] `.env.example`, `requirements.txt`, and the runbook document every new setting.
- [ ] Phase 3 boxes in [specs/roadmap.md](file:///c:/Users/lemon/integration/specs/roadmap.md#L81-L117) are ticked, and `specs/2026-10-23-langchain-triage-agent/` is committed with the implementation.
- [ ] Phase 2 work is committed on its own branch before opening this PR, or the PR description explicitly calls out the inherited Phase 2 diff.
