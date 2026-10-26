# Feature Requirements: PostgreSQL Persistence & Email Ingestion Pipeline

- **Feature Directory**: `specs/2026-10-23-email-ingestion-pipeline`
- **Roadmap Reference**: [Phase 2 (2.1 - 2.5)](file:///c:/Users/lemon/integration/specs/roadmap.md#L45-L78)
- **Status**: Draft / Ready for Implementation
- **Target Branch**: `feature/phase-2-email-ingestion-pipeline`

---

## 1. Executive Summary & Context

With Phase 1 complete (providing environment configuration, Google OAuth 2.0 authorization, AES-GCM token encryption, and basic Gmail profile connection verification), the **Gmail AI Email Triage Agent** now requires a resilient, relational data foundation and an automated ingestion pipeline.

This feature delivers **Phase 2: PostgreSQL Persistence & Email Ingestion Pipeline**:
1. Expands the cloud **Supabase PostgreSQL** schema to model conversation threads (`threads`) and individual messages (`messages`) with strict foreign keys and idempotency constraints.
2. Implements a robust **MIME parser** and content sanitizer that parses multipart RFC 2822 email structures, safely extracts plain text and HTML bodies, strips malicious code, and extracts attachment metadata without retaining heavy binary blobs.
3. Establishes an **idempotent delta ingestion engine** using Gmail's `users.history.list` API and cursor-based `historyId` tracking, with an initial backfill strategy targeting the most recent 50 threads.
4. Integrates real-time mailbox push notifications via **Google Cloud Pub/Sub** using **Google Application Default Credentials (gcloud ADC)** already authenticated in the terminal environment, exposing an authenticated push webhook endpoint (`POST /api/v1/webhook/pubsub`) and a manual fallback sync endpoint (`POST /api/v1/gmail/sync`).

---

## 2. In-Scope vs. Out-of-Scope

### In-Scope
- **Database Schema Expansion (Supabase Cloud PostgreSQL)**:
  - Connects to existing Supabase instance using SQLAlchemy 2.0 async engine and `asyncpg` via `DATABASE_URL`.
  - Defines and manages declarative models for:
    - `Thread`: Gmail conversation thread metadata, snippet, `history_id`, and timestamps.
    - `Message`: Individual email messages with sender, recipient, date, subject, plain text body, sanitized HTML body, attachment metadata (JSONB), and raw headers.
  - Enforces database-level idempotency via unique constraints on `gmail_thread_id` and `gmail_message_id`.
- **MIME Parser & Content Sanitizer (`app/services/mime_parser.py`)**:
  - Decodes base64url payloads from the Gmail REST API v1.
  - Recursively traverses multipart message payloads (`multipart/mixed`, `multipart/alternative`, `multipart/related`).
  - Converts HTML content to clean, readable plain text/markdown while stripping `<script>`, `<iframe>`, and active dangerous tags.
  - Extracts attachment metadata (filename, mime type, size in bytes) without persisting raw binary data.
- **Gmail Ingestion & Delta Sync Service (`app/services/ingestion.py`)**:
  - Initial backfill mechanism: Ingests the most recent 50 threads (or past 14 days) upon connection.
  - Incremental delta synchronization using `users.history.list` with cursor management via `accounts.history_id`.
  - Idempotent upsert operations: Re-ingesting an existing message or updating an ongoing thread executes safely without duplicate rows or runtime errors.
- **Google Cloud Pub/Sub & Webhook Integration**:
  - Uses terminal `gcloud auth` / Application Default Credentials (ADC) via `google.auth.default()` for GCP Pub/Sub client interaction.
  - Gmail `users.watch` registration utility with topic configuration (`projects/{project_id}/topics/{topic}`).
  - Webhook endpoint: `POST /api/v1/webhook/pubsub` to receive push notifications forwarded by Pub/Sub (or local tunnel via ngrok/Cloudflare).
  - Manual sync endpoint: `POST /api/v1/gmail/sync` for on-demand trigger and backfill.
- **Dependency Management**:
  - Updates pinned dependencies in `requirements.txt` (e.g. `google-cloud-pubsub`, `beautifulsoup4`, `html2text`, `lxml`).

### Out-of-Scope (Deferred to Later Phases)
- LangChain Tool Agent invocation, Gemini 3.8 Flash triage analysis, and action item extraction ([Phase 3](file:///c:/Users/lemon/integration/specs/roadmap.md#L81-L117)).
- `pgvector` semantic embeddings generation and cosine similarity search across emails ([Phase 4](file:///c:/Users/lemon/integration/specs/roadmap.md#L120-L145)).
- Frontend dashboard query endpoints for daily digests and urgent triage feeds ([Phase 4](file:///c:/Users/lemon/integration/specs/roadmap.md#L128-L135)).
- Sending, drafting, or modifying emails in the Gmail mailbox (Strictly Out-of-Scope as per Constitution Principle 1).

---

## 3. Architecture & Technical Decisions

1. **Database Infrastructure (Supabase Cloud PostgreSQL)**:
   - **Decision**: Continue using Supabase Cloud PostgreSQL directly via `DATABASE_URL` with `asyncpg`. No local Docker daemon required.
   - **Rationale**: Supabase provides managed PostgreSQL 16 with pre-installed `pgvector`, eliminating local container management and keeping developer setup lightweight.
2. **GCP Authentication for Pub/Sub (Terminal `gcloud auth` / ADC)**:
   - **Decision**: Authenticate Google Cloud Pub/Sub operations via Application Default Credentials (`google.auth.default()`), which transparently loads credentials from the user's active `gcloud auth` session.
   - **Rationale**: The user has already run `gcloud auth` in their terminal, removing the friction of downloading and managing sensitive JSON service account key files.
3. **Real-time Push with Webhook & Delta Polling Fallback**:
   - **Decision**: Support Pub/Sub push notifications via `POST /api/v1/webhook/pubsub` while maintaining `POST /api/v1/gmail/sync` for manual or scheduled delta syncs.
   - **Rationale**: Guarantees zero missed emails even if the local development tunnel drops or Pub/Sub experiences temporary delivery delays.
4. **Initial Backfill Window (Recent 50 Threads)**:
   - **Decision**: Ingest the 50 most recent threads on initial account connection.
   - **Rationale**: Balances immediate inbox intelligence utility against API quota consumption and execution time during onboarding.
5. **Idempotency & Data Integrity**:
   - **Decision**: Enforce unique constraints on `threads.gmail_thread_id` and `messages.gmail_message_id`. Use PostgreSQL `ON CONFLICT DO UPDATE` or SQLAlchemy merge semantics.
   - **Rationale**: Prevents duplicate records from concurrent webhook push notifications and manual sync retries.
6. **MIME Sanitization & Attachment Pruning**:
   - **Decision**: Clean HTML with `beautifulsoup4`/`html2text` to strip scripts and styles; record attachment metadata in `JSONB` without storing binary payloads.
   - **Rationale**: Safeguards downstream LLM context windows against XSS and token bloat while conserving database storage.

---

## 4. Key Schemas & Models

### Model: `threads` (`app/models/thread.py`)
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | UUID | Primary Key, default `uuid4` | Internal thread identifier |
| `gmail_thread_id` | String(64) | Unique, Index, Not Null | Gmail-assigned thread ID |
| `account_id` | UUID | Foreign Key -> `accounts.id`, Not Null | Owning account reference |
| `subject` | String(500) | Nullable | Primary conversation subject |
| `snippet` | Text | Nullable | Latest thread snippet preview |
| `history_id` | String(64) | Nullable | Most recent Gmail history ID for thread |
| `last_message_at` | DateTime (UTC)| Nullable | Timestamp of latest message in thread |
| `summary` | Text | Nullable | Executive briefing (populated in Phase 3) |
| `created_at` | DateTime (UTC)| Default UTC now | Row creation timestamp |
| `updated_at` | DateTime (UTC)| Default UTC now, onupdate | Row update timestamp |

### Model: `messages` (`app/models/message.py`)
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | UUID | Primary Key, default `uuid4` | Internal message identifier |
| `gmail_message_id` | String(64) | Unique, Index, Not Null | Gmail-assigned message ID |
| `thread_id` | UUID | Foreign Key -> `threads.id`, Not Null | Parent thread reference |
| `sender` | String(255) | Not Null, Index | From header (display name + email) |
| `recipient` | String(255) | Not Null | To header |
| `subject` | String(500) | Nullable | Email subject line |
| `date` | DateTime (UTC)| Not Null, Index | RFC 2822 sent date |
| `snippet` | Text | Nullable | Gmail snippet preview |
| `body_text` | Text | Nullable | Parsed clean plain text content |
| `body_html` | Text | Nullable | Sanitized HTML content (safe tags only) |
| `has_attachments` | Boolean | Default False | Indicator of file attachments |
| `attachment_names` | JSONB | Default `[]` | List of metadata: `[{filename, mime_type, size}]` |
| `raw_headers` | JSONB | Default `{}` | Extracted headers (Message-ID, References, etc.) |
| `created_at` | DateTime (UTC)| Default UTC now | Record creation timestamp |

### API Schemas (`app/schemas/ingestion.py`)
- `SyncRequest`: `email: Optional[str]`, `max_threads: Optional[int] = 50`
- `SyncResponse`: `account_email: str`, `threads_synced: int`, `messages_synced: int`, `new_history_id: str`
- `PubSubPushPayload`: Google Pub/Sub push envelope (`message.data`, `message.messageId`, `subscription`)
- `WatchRegistrationResponse`: `history_id: str`, `expiration: str`, `status: str`
