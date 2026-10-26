# Validation & Testing Guide: Frontend Data Views, Digest APIs & Resilience Guardrails

- **Feature Directory**: `specs/2026-10-26-frontend-views-digest-search`
- **Target Branch**: `feature/phase-4-frontend-views-digest-search`
- **Specification Reference**: [requirements.md](file:///c:/Users/lemon/integration/specs/2026-10-26-frontend-views-digest-search/requirements.md) | [plan.md](file:///c:/Users/lemon/integration/specs/2026-10-26-frontend-views-digest-search/plan.md)
- **Constitution Reference**: [specs/mission.md](file:///c:/Users/lemon/integration/specs/mission.md) | [specs/tech-stack.md](file:///c:/Users/lemon/integration/specs/tech-stack.md) | [specs/roadmap.md](file:///c:/Users/lemon/integration/specs/roadmap.md#L128-L145)
- **Testing Approach**: Dual-Layer Verification (hermetic SQLite pytest suite + live Supabase walkthrough).

---

## 1. Prerequisites

- [x] Python 3.11+ environment with dependencies installed:
  ```powershell
  .venv\Scripts\Activate.ps1
  pip install -r requirements.txt
  ```
- [x] `DATABASE_URL` configured in `.env` pointing to PostgreSQL / Supabase instance.
- [x] Stored inbox data from Phase 2 and triage analyses from Phase 3 present in the database.

---

## 2. Layer 1: Hermetic Pytest Suite (CI-Ready)

Execution:
```powershell
pytest tests/ -v
```

### 2.1 Digest & Triage REST APIs (`tests/test_digest_api.py`)
- [x] `GET /api/v1/digest/today` returns HTTP 200 with accurate urgency score distribution (1 to 5) and category breakdown.
- [x] `GET /api/v1/action-items` returns pending action items ordered by deadline (`deadline ASC NULLS LAST`).
- [x] `GET /api/v1/action-items?status=done` filters correctly to completed items.
- [x] `GET /api/v1/threads/{thread_id}/triage` returns full executive briefing, decisions, pending actions, and chronological messages.
- [x] Requesting an unknown thread ID returns HTTP 404.

### 2.2 Resilience & Guardrails (`tests/test_guardrails.py`)
- [x] `with_retry` decorator triggers exponential backoff on simulated 429/503 errors and succeeds if a later attempt succeeds.
- [x] `with_retry` exhausts attempts and re-raises the underlying error after max failed attempts.
- [x] Read-only guardrail confirms active scopes are exclusively `gmail.readonly` and `userinfo.email`.
- [x] Read-only guardrail dynamically checks `GmailClient` methods; attempting to inject a mock send or delete method causes startup validation to raise `ReadOnlySafetyViolationError`.

---

## 3. Layer 2: Live Verification Walkthrough (`scripts/verify_phase4.py`)

Run against live Supabase PostgreSQL:

```powershell
python scripts/verify_phase4.py
```

### 3.1 Database Views & Indexes Verification
- [x] Verifies database views `v_urgent_triage_feed`, `v_pending_action_items`, and `v_today_digest` can be queried via raw SQL on PostgreSQL.
- [x] Verifies composite indexes `ix_email_analyses_urgency_created` and `ix_action_items_status_deadline` are present and active.

### 3.2 Live REST API Verification
- [x] `curl -s http://localhost:8000/api/v1/digest/today | jq .` returns valid today's digest JSON.
- [x] `curl -s http://localhost:8000/api/v1/action-items | jq .` returns pending action items.
- [x] `curl -s http://localhost:8000/api/v1/threads/<THREAD_ID>/triage | jq .` returns executive briefing.

### 3.3 Milestone 4 Merge Gate
- [x] All Layer 1 hermetic tests pass (`pytest tests/ -v`).
- [x] Layer 2 live verification script passes without errors (`python scripts/verify_phase4.py`).
- [x] Read-only safety guardrail passes and confirms 100% read-only integrity.
- [ ] Zero uncommitted changes or linting errors.

