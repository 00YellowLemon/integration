# Validation & Testing Guide: PostgreSQL Persistence & Email Ingestion Pipeline

- **Feature Directory**: `specs/2026-10-23-email-ingestion-pipeline`
- **Target Branch**: `feature/phase-2-email-ingestion-pipeline`
- **Specification Reference**: [requirements.md](file:///c:/Users/lemon/integration/specs/2026-10-23-email-ingestion-pipeline/requirements.md) | [plan.md](file:///c:/Users/lemon/integration/specs/2026-10-23-email-ingestion-pipeline/plan.md)
- **Constitution Reference**: [specs/mission.md](file:///c:/Users/lemon/integration/specs/mission.md) | [specs/tech-stack.md](file:///c:/Users/lemon/integration/specs/tech-stack.md) | [specs/roadmap.md](file:///c:/Users/lemon/integration/specs/roadmap.md#L45-L78)
- **Testing Approach**: Dual-Layer Verification (Hermetic Pytest Suite with Mocks + Live Supabase & Gmail Delta Sync Verification)

---

## 1. Prerequisites Checklist

Before executing validation, ensure:
- [ ] Python 3.11+ installed and active in virtual environment:
  ```bash
  .venv\Scripts\activate   # On Windows
  pip install -r requirements.txt
  ```
- [ ] Terminal authenticated via Google Cloud CLI (Application Default Credentials):
  ```bash
  gcloud auth application-default login
  ```
- [ ] `.env` file configured with valid credentials:
  - `DATABASE_URL`: Supabase PostgreSQL async connection string (`postgresql+asyncpg://...`).
  - `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
  - `APP_SECRET_KEY`: 32-byte secret for token encryption.
  - `GCP_PROJECT_ID`: Your GCP project ID (or auto-discovered via ADC).
  - `PUBSUB_TOPIC_NAME`: Pub/Sub topic name configured for Gmail push notifications.
- [ ] An active account exists in Supabase (completed from Phase 1 OAuth handshake).

---

## 2. Layer 1: Automated Pytest Suite (Hermetic / CI-Ready)

The automated test suite verifies all logic without making live external API calls or modifying live database state.

### Test Execution Command
```bash
pytest tests/ -v
```

### 2.1 MIME Parser & Sanitization Tests (`tests/test_mime_parser.py`)
- [ ] **Test Plain Text Extraction**: Parses simple `text/plain` email body without encoding artifacts.
- [ ] **Test Multipart Alternative**: Accurately extracts clean body from multipart message containing both `text/plain` and `text/html`.
- [ ] **Test HTML Sanitization & Conversion**:
  - Injects malicious `<script>alert('xss')</script>` and `<iframe>` tags into HTML payload.
  - Verifies that sanitized output strips scripts, styles, and dangerous tags while converting body structure into readable plain markdown.
- [ ] **Test Attachment Metadata Extraction**:
  - Injects mock attachment part with `filename="invoice.pdf"`, `mimeType="application/pdf"`, and `body.size=1048576`.
  - Verifies that parsed metadata returns `{"filename": "invoice.pdf", "mime_type": "application/pdf", "size": 1048576}` without storing raw bytes.
- [ ] **Test RFC 2822 Date Parsing**:
  - Tests various date formats (with timezone offsets, day names) and verifies correct UTC conversion.

### 2.2 Relational Model & Idempotency Tests (`tests/test_ingestion.py`)
- [ ] **Test Thread & Message Creation**:
  - Mock Gmail thread payload containing 2 messages.
  - Ingestion creates 1 `Thread` record and 2 `Message` records linked by `thread_id`.
- [ ] **Test Ingestion Idempotency**:
  - Re-ingest the exact same Gmail thread and message payload.
  - Verify that database row count remains unchanged and no unique constraint violation is raised.
- [ ] **Test Delta Sync Processing**:
  - Provide mocked `users.history.list` response containing `messagesAdded`.
  - Verify delta sync fetches only new message details and updates `accounts.history_id`.
- [ ] **Test History ID Expiry Recovery**:
  - Simulate HTTP 404 (`historyId expired` error from Gmail API).
  - Verify ingestion automatically triggers a fresh backfill instead of crashing.

### 2.3 Webhook & Pub/Sub Router Tests (`tests/test_webhook.py`)
- [ ] **Test Valid Pub/Sub Push Envelope**:
  - Send POST request to `/api/v1/webhook/pubsub` with base64-encoded Pub/Sub message data `{"emailAddress": "user@gmail.com", "historyId": "12345"}`.
  - Verify endpoint returns HTTP 204 No Content.
- [ ] **Test Malformed Pub/Sub Payload**:
  - Send POST request with missing `message.data`.
  - Verify endpoint returns HTTP 400 Bad Request.
- [ ] **Test Non-Existent Account Webhook**:
  - Send notification for an unregistered email address.
  - Verify endpoint responds cleanly with HTTP 404 or acknowledges without crashing.

---

## 3. Layer 2: Live Ingestion & Sync Walkthrough

Verify end-to-end data flow using live Supabase PostgreSQL, gcloud ADC, and your connected Gmail account.

### Step 3.1: Table Schema Verification
1. Start the FastAPI server:
   ```bash
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
   ```
2. Verify startup logs confirm database connection and creation of `threads` and `messages` tables.
3. In the Supabase SQL Editor, verify table structures:
   ```sql
   SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';
   ```
   - Confirms `accounts`, `threads`, and `messages` are present.

### Step 3.2: Execute Initial Backfill
Trigger manual sync via API:
```bash
curl -X POST http://127.0.0.1:8000/api/v1/gmail/sync -H "Content-Type: application/json" -d '{"max_threads": 50}'
```
- [ ] **HTTP Status**: 200 OK.
- [ ] **Response Body**:
  ```json
  {
    "status": "success",
    "account_email": "user@gmail.com",
    "threads_synced": 50,
    "messages_synced": 112,
    "new_history_id": "18492042"
  }
  ```
- [ ] **Database Verification**: Query Supabase:
  ```sql
  SELECT COUNT(*) FROM threads;
  SELECT COUNT(*) FROM messages;
  SELECT subject, sender, date, has_attachments, attachment_names FROM messages LIMIT 5;
  ```
  - Thread count matches synced count.
  - Messages have sender, subject, UTC timestamp, and clean `body_text`.

### Step 3.3: Idempotency Live Test
Re-run the exact same sync command immediately:
```bash
curl -X POST http://127.0.0.1:8000/api/v1/gmail/sync -H "Content-Type: application/json" -d '{"max_threads": 50}'
```
- [ ] **HTTP Status**: 200 OK.
- [ ] **Database Verification**: Check that `COUNT(*)` in `threads` and `messages` did not double and no duplicate key errors were thrown.

### Step 3.4: Pub/Sub Webhook Verification (Optional Local Tunnel)
1. If using ngrok or Cloudflare tunnel:
   ```bash
   ngrok http 8000
   ```
2. Register Gmail watch:
   ```bash
   curl -X POST http://127.0.0.1:8000/api/v1/gmail/watch
   ```
3. Send an email to the connected Gmail account from another inbox.
4. Observe FastAPI terminal logs:
   - Pub/Sub push notification received at `POST /api/v1/webhook/pubsub`.
   - Delta sync automatically triggered.
   - New message ingested into `messages` table.

### Step 3.5: Run Interactive Verification CLI Tool
Run the verification script:
```bash
python scripts/verify_ingestion.py
```
- [ ] Script confirms Supabase connectivity and displays account details.
- [ ] Displays count of stored threads and messages.
- [ ] Prints preview of latest 5 ingested email threads.

---

## 4. Milestone 2 Completion & Merge Readiness Checklist

Before merging branch `feature/phase-2-email-ingestion-pipeline` into `main`:
- [ ] All unit and integration tests pass cleanly (`pytest -v`).
- [ ] Supabase tables `threads` and `messages` persist data with correct foreign keys.
- [ ] Ingestion is fully idempotent: syncing duplicate messages causes zero row duplication.
- [ ] MIME parser safely strips scripts and sanitizes HTML bodies into clean markdown.
- [ ] Large attachments are not saved in raw binary form (metadata only).
- [ ] Gmail `gmail.readonly` scope is strictly preserved (zero send/draft/delete methods exist).
- [ ] `requirements.txt` is updated and frozen.
- [ ] Branch commits reference [requirements.md](file:///c:/Users/lemon/integration/specs/2026-10-23-email-ingestion-pipeline/requirements.md) and [plan.md](file:///c:/Users/lemon/integration/specs/2026-10-23-email-ingestion-pipeline/plan.md).
