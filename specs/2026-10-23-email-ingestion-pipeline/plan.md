# Implementation Plan: PostgreSQL Persistence & Email Ingestion Pipeline

- **Feature Directory**: `specs/2026-10-23-email-ingestion-pipeline`
- **Target Branch**: `feature/phase-2-email-ingestion-pipeline`
- **Specification Reference**: [requirements.md](file:///c:/Users/lemon/integration/specs/2026-10-23-email-ingestion-pipeline/requirements.md)
- **Constitution Guidance**: [specs/mission.md](file:///c:/Users/lemon/integration/specs/mission.md) | [specs/tech-stack.md](file:///c:/Users/lemon/integration/specs/tech-stack.md) | [specs/roadmap.md](file:///c:/Users/lemon/integration/specs/roadmap.md#L45-L78)

---

## Task Group 1: Dependencies & Database Models Expansion

- [x] **Task 1.1: Update `requirements.txt` with Ingestion Dependencies**
  - Add required dependencies for Phase 2:
    - `google-cloud-pubsub>=2.19.0,<3.0.0` (Pub/Sub API client using ADC)
    - `beautifulsoup4>=4.12.3,<5.0.0` (HTML parsing and tag sanitization)
    - `html2text>=2024.2.26` (Clean HTML-to-plain text conversion)
    - `lxml>=5.2.0,<6.0.0` (High performance HTML parsing backend)
    - `python-dateutil>=2.9.0` (Robust RFC 2822 date parsing)

- [x] **Task 1.2: Implement `Thread` Model (`app/models/thread.py`)**
  - Create SQLAlchemy model `Thread` in `app/models/thread.py`:
    - `id`: UUID (Primary Key, default `uuid4`)
    - `gmail_thread_id`: String(64), unique, indexed, non-null
    - `account_id`: UUID, foreign key to `accounts.id`, indexed, non-null
    - `subject`: String(500), nullable
    - `snippet`: Text, nullable
    - `history_id`: String(64), nullable
    - `last_message_at`: DateTime(timezone=True), nullable
    - `summary`: Text, nullable (reserved for Phase 3)
    - `created_at`, `updated_at`: DateTime(timezone=True)
    - Relationships: `account` (Many-to-One), `messages` (One-to-Many, cascade="all, delete-orphan")

- [x] **Task 1.3: Implement `Message` Model (`app/models/message.py`)**
  - Create SQLAlchemy model `Message` in `app/models/message.py`:
    - `id`: UUID (Primary Key, default `uuid4`)
    - `gmail_message_id`: String(64), unique, indexed, non-null
    - `thread_id`: UUID, foreign key to `threads.id`, indexed, non-null
    - `sender`: String(255), indexed, non-null
    - `recipient`: String(255), non-null
    - `subject`: String(500), nullable
    - `date`: DateTime(timezone=True), indexed, non-null
    - `snippet`: Text, nullable
    - `body_text`: Text, nullable
    - `body_html`: Text, nullable (sanitized)
    - `has_attachments`: Boolean, default False
    - `attachment_names`: JSONB (or JSON for cross-dialect compatibility), default `list`
    - `raw_headers`: JSONB, default `dict`
    - `created_at`: DateTime(timezone=True)
    - Relationships: `thread` (Many-to-One)

- [x] **Task 1.4: Update Model Registrations & DB Initialization (`app/db/base.py` & `app/models/__init__.py`)**
  - Export `Thread` and `Message` in `app/models/__init__.py`.
  - Update `init_db()` in `app/db/base.py` to ensure `threads` and `messages` tables are created idempotently on Supabase.

---

## Task Group 2: MIME Parsing & Content Sanitization

- [x] **Task 2.1: Implement Base64 and Header Parsing Utilities (`app/services/mime_parser.py`)**
  - Decode URL-safe base64 data (`base64.urlsafe_b64decode`).
  - Parse RFC 2822 dates using `email.utils.parsedate_to_datetime` with timezone fallback to UTC.
  - Parse header lists into structured dictionaries (`From`, `To`, `Subject`, `Date`, `Message-ID`, `References`).
  - Extract display name and email address cleanly from `From` and `To` headers.

- [x] **Task 2.2: Implement Multipart Payload Traversal & Body Extraction**
  - Recursively traverse nested Gmail payload parts (`parts` list in message resource).
  - Extract `text/plain` and `text/html` parts handling different character encodings (UTF-8, ISO-8859-1).
  - Prioritize clean plain text; if only HTML is available, convert HTML to clean markdown text using `html2text`.

- [x] **Task 2.3: Implement HTML Sanitizer & Attachment Metadata Extractor**
  - Sanitize HTML with `BeautifulSoup`:
    - Strip `<script>`, `<style>`, `<iframe>`, `<object>`, `<embed>`, `<form>`, and event attributes (`onload`, `onclick`, etc.).
  - Identify attachments from message parts having `filename` and non-inline dispositions.
  - Extract metadata dictionary for each attachment: `filename`, `mime_type`, `size` (in bytes), and `attachment_id` without downloading or buffering raw file bytes.

---

## Task Group 3: Gmail Sync & Ingestion Engine

- [x] **Task 3.1: Extend `GmailClient` with Fetch & History APIs (`app/services/gmail.py`)**
  - Add `list_threads(max_results=50, page_token=None)` calling `users.threads.list`.
  - Add `get_thread(thread_id: str)` calling `users.threads.get(id=thread_id, format='full')`.
  - Add `list_history(start_history_id: str, page_token=None)` calling `users.history.list`.
  - Add `register_watch(topic_name: str, label_ids=None)` calling `users.watch`.
  - Add `stop_watch()` calling `users.stop`.

- [x] **Task 3.2: Implement Ingestion Service (`app/services/ingestion.py`)**
  - `ingest_thread(db: AsyncSession, account_id: UUID, gmail_thread_resource: dict) -> Thread`:
    - Upsert thread record by `gmail_thread_id`.
    - Iterate through messages in thread payload.
    - Parse each message via `MIMEParser`.
    - Upsert message record by `gmail_message_id` with deduplication.
  - `initial_backfill(db: AsyncSession, account: Account, max_threads: int = 50) -> SyncStats`:
    - Fetch the recent 50 threads from Gmail.
    - Ingest threads and messages in atomic batches.
    - Update `accounts.history_id` with latest mailbox checkpoint.

- [x] **Task 3.3: Implement Delta Sync Engine (`app/services/ingestion.py`)**
  - `delta_sync(db: AsyncSession, account: Account) -> SyncStats`:
    - If `account.history_id` is present, call `users.history.list(startHistoryId=account.history_id)`.
    - Handle `history_id` expiration (HTTP 404 / `historyId not found` by triggering backfill).
    - Collect newly added messages (`messagesAdded`) and modified threads.
    - Fetch full thread/message payloads and upsert into database.
    - Persist the updated `historyId` to `accounts.history_id`.

---

## Task Group 4: Google Cloud Pub/Sub & Webhook Integration

- [x] **Task 4.1: Pub/Sub Setup Helper using gcloud ADC (`app/core/pubsub.py`)**
  - Initialize Pub/Sub subscriber client using Google Application Default Credentials (`google.auth.default()`).
  - Add utility to verify Pub/Sub topic and create subscription if necessary.
  - Support configuration keys in `app/core/config.py`:
    - `GCP_PROJECT_ID: Optional[str]` (auto-discovered from ADC if omitted)
    - `PUBSUB_TOPIC_NAME: str = "gmail-notifications"`
    - `PUBSUB_SUBSCRIPTION_NAME: str = "gmail-notifications-sub"`

- [x] **Task 4.2: Implement Pub/Sub Push Webhook Receiver (`app/api/webhook.py`)**
  - Create endpoint `POST /api/v1/webhook/pubsub`:
    - Parse standard Google Cloud Pub/Sub push JSON structure (`message.data`, `message.messageId`).
    - Base64-decode `message.data` to extract Gmail notification payload (`emailAddress`, `historyId`).
    - Validate that the notification corresponds to an active stored account.
    - Trigger `delta_sync` asynchronously in the background.
    - Return HTTP 204 No Content (acknowledging push message receipt to Google Pub/Sub).

- [x] **Task 4.3: Implement Manual Sync & Watch Endpoints (`app/api/gmail.py`)**
  - `POST /api/v1/gmail/sync`: Triggers delta sync or backfill on-demand; returns sync summary (`threads_synced`, `messages_synced`, `new_history_id`).
  - `POST /api/v1/gmail/watch`: Calls `users.watch` with configured Pub/Sub topic and returns expiration timestamp.
  - Mount new webhook router in `app/main.py`.

---

## Task Group 5: Automated Testing & Milestone 2 Verification Suite

- [x] **Task 5.1: Unit Tests for MIME Parser & Sanitizer (`tests/test_mime_parser.py`)**
  - Test plain text extraction from singlepart message.
  - Test multipart/alternative email parsing (plain text preferred or sanitized HTML converted).
  - Test malicious script tag removal from HTML body.
  - Test attachment metadata extraction (name, size, MIME type).
  - Test RFC 2822 date parsing edge cases (timezones, offsets).

- [x] **Task 5.2: Ingestion & Idempotency Tests (`tests/test_ingestion.py`)**
  - Test `initial_backfill` populates `threads` and `messages` tables with mocked Gmail thread data.
  - Test idempotent insertion: Re-ingesting the exact same message ID updates safely without duplicate row errors.
  - Test `delta_sync` parses `users.history.list` and fetches only updated items.
  - Test `accounts.history_id` updates accurately post-sync.

- [x] **Task 5.3: Webhook & Pub/Sub Payload Tests (`tests/test_webhook.py`)**
  - Test `POST /api/v1/webhook/pubsub` decodes Pub/Sub message data and triggers sync.
  - Test invalid or missing message data returns 400 Bad Request.
  - Test unknown email in payload returns graceful 404 without crashing.

- [x] **Task 5.4: Interactive Sync & Live Ingestion CLI (`scripts/verify_ingestion.py`)**
  - Interactive verification script:
    - Verifies Supabase connection and displays existing accounts.
    - Executes `initial_backfill` (or delta sync) on the active connected account.
    - Displays a formatted summary table of ingested threads and messages.
    - Registers or tests Gmail Pub/Sub watch status.
