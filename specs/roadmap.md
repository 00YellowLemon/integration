# Constitution: Implementation Roadmap

This roadmap breaks down the construction of the **Gmail AI Assistant** into **micro-phases** of work. Each phase is small, self-contained, and testable before proceeding to the next.

---

## Phase 1: Environment Scaffolding & Google OAuth 2.0 Integration

### Phase 1.1: Project Skeleton & Configuration Management
- [ ] Initialize Python project structure (`app/api/`, `app/core/`, `app/services/`, `app/models/`, `specs/`, `tests/`).
- [ ] Setup `requirements.txt` / `pyproject.toml` with dependencies: `fastapi`, `uvicorn`, `pydantic`, `pydantic-settings`, `httpx`, `google-auth-oauthlib`, `google-api-python-client`.
- [ ] Implement `app/core/config.py` using `pydantic-settings` to load Google credentials (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `APP_SECRET_KEY`).
- [ ] Create `.env.example` template with step-by-step setup instructions.
- **Verification**: App boots with valid configs and fails gracefully when required environment variables are missing.

### Phase 1.2: Google OAuth 2.0 Authorization Flow
- [ ] Create FastAPI authentication router in `app/api/auth.py`.
- [ ] Implement `GET /auth/login` to generate Google OAuth consent URL with:
  - Scopes: `https://www.googleapis.com/auth/gmail.readonly`, `https://www.googleapis.com/auth/userinfo.email`.
  - Parameters: `access_type="offline"`, `prompt="consent"`, secure CSRF `state`.
- [ ] Implement `GET /auth/callback` to handle Google redirect:
  - Verify CSRF `state`.
  - Exchange authorization code for access and refresh tokens.
- **Verification**: Access `/auth/login` in browser, authorize with personal Gmail account, and confirm code exchange yields valid tokens.

### Phase 1.3: Token Persistence & Auto-Refresh Mechanism
- [ ] Build token storage service to manage access and refresh tokens.
- [ ] Implement automatic refresh logic when access token expires or is near expiry (using `google.auth.transport.requests`).
- [ ] Provide utility function to return authenticated Google credentials for API calls.
- **Verification**: Run unit test with an expired access token and verify that the refresh token successfully fetches a new access token without re-prompting the user.

### Phase 1.4: Gmail API Client & Connection Verification
- [ ] Build async-compatible `GmailClient` in `app/services/gmail.py`.
- [ ] Implement profile handshake method:
  - `get_profile()` calling Gmail API `users.getProfile(userId='me')`.
- [ ] Create `GET /api/v1/gmail/status` endpoint returning connected email address, total messages, and thread count.
- **Verification**: Calling `/api/v1/gmail/status` returns HTTP 200 with accurate user profile stats for the connected Gmail account.

### Phase 1.5: Milestone 1 End-to-End Connection Loop
- [ ] Create unified startup health check and token validation.
- **Milestone 1 Test**: Authenticate via OAuth 2.0 flow and successfully fetch profile details from Gmail API.

---

## Phase 2: PostgreSQL Persistence & Email Ingestion Pipeline

### Phase 2.1: PostgreSQL & Async Engine Setup
- [ ] Add `docker-compose.yml` for local PostgreSQL with `pgvector` extension.
- [ ] Setup SQLAlchemy 2.0 async engine and session factory (`asyncpg`).
- [ ] Create base database configuration and initialization lifecycle in FastAPI.
- **Verification**: Database container launches, connects via async session, and executes `SELECT 1`.

### Phase 2.2: Relational Schema & Idempotent Ingestion
- [ ] Define SQLAlchemy models in `app/models/`:
  - `Account` (`id`, `email`, `access_token`, `refresh_token`, `token_expiry`, `history_id`, `created_at`)
  - `Thread` (`id`, `gmail_thread_id`, `snippet`, `history_id`, `last_message_at`)
  - `Message` (`id`, `gmail_message_id`, `thread_id`, `sender`, `recipient`, `subject`, `date`, `snippet`, `body_text`, `body_html`, `has_attachments`)
- [ ] Implement idempotent message insertion using unique constraint on `gmail_message_id`.
- **Verification**: Attempting to insert the same Gmail message ID twice updates or ignores safely without duplicate rows or errors.

### Phase 2.3: MIME Parser & Content Sanitization
- [ ] Build MIME message parser in `app/services/mime_parser.py`:
  - Decode standard multi-part emails (plain text vs. HTML).
  - Strip dangerous script tags and convert HTML to readable markdown/plain text.
  - Extract attachment metadata (filename, mime type, size in bytes) without saving huge payloads.
- **Verification**: Test with diverse email structures (plain text, multipart/alternative, multipart/mixed) and verify clean body extraction.

### Phase 2.4: Real-time Ingestion via Google Cloud Pub/Sub & Sync Cursor
- [ ] Implement `users.watch` registration utility for Gmail mailbox push notifications.
- [ ] Create `POST /api/v1/webhook/pubsub` endpoint to receive Google Cloud Pub/Sub push notifications.
- [ ] Implement delta sync worker using Gmail `users.history.list` starting from stored `history_id`.
- [ ] Provide fallback polling endpoint `POST /api/v1/gmail/sync` for manual or cron-based delta sync.
- **Verification**: Trigger a manual sync or send a test email to the account, confirming new messages are fetched and stored in PostgreSQL.

### Phase 2.5: Milestone 2 Ingestion Verification
- **Milestone 2 Test**: Connect account, run sync, and verify that recent Gmail threads and messages are populated accurately in PostgreSQL.

---

## Phase 3: Gemini 3.8 Flash Analysis & Intelligence Engine

### Phase 3.1: Gemini 3.8 Flash Provider Setup
- [ ] Install `langchain-google-genai` and configure API key (`GEMINI_API_KEY`).
- [ ] Initialize `ChatGoogleGenerativeAI(model="gemini-3.8-flash")`.
- [ ] Define Pydantic output schemas for structured email analysis (`EmailAnalysisResult`, `ActionItem`, `CategoryEnum`).
- **Verification**: Run standalone script verifying Gemini 3.8 Flash analyzes a sample email and outputs valid structured JSON conforming to the Pydantic schema.

### Phase 3.2: Automated Triage & Category Classification
- [ ] Craft system prompts for email classification into predefined categories:
  - `Urgent`, `Action Required`, `Invoices / Receipts`, `Meeting Request`, `Newsletter / Updates`, `General / Informational`.
- [ ] Assign confidence score and brief rationale.
- [ ] Store classification in `email_analyses` table.
- **Verification**: Pass sample emails of different types; verify classifications match expected categories accurately.

### Phase 3.3: Action Items & Deadline Extraction
- [ ] Implement entity extraction for:
  - Action items (tasks requested of the user).
  - Explicit and implied deadlines (dates, times).
  - Contact / organization details.
- [ ] Save extracted action items into `action_items` table linked to `message_id`.
- **Verification**: Send an email with "Please submit the report by Friday at 3 PM"; verify action item and deadline are extracted correctly.

### Phase 3.4: Thread Summarization & Executive Briefings
- [ ] Build multi-message thread synthesis pipeline:
  - Combine sequential messages within a thread into a chronological digest.
  - Generate an executive summary covering what was discussed, decisions made, and pending questions.
- **Verification**: Run summarizer on a 5-message email thread; verify concise executive summary accurately reflects the entire conversation.

### Phase 3.5: Milestone 3 Intelligence Pipeline Test
- **Milestone 3 Test**: Process an unread email through the pipeline: Ingestion -> Parsing -> Gemini Analysis -> Action Items & Summary stored in DB.

---

## Phase 4: Semantic Search, Digests & Query APIs

### Phase 4.1: Vector Embeddings & Semantic Search
- [ ] Add `embedding` column (`VECTOR(768)`) to `messages` or `email_analyses` using `pgvector`.
- [ ] Integrate Gemini embedding model to generate embeddings for processed email contents and summaries.
- [ ] Implement `search_emails(query: str, limit: int)` performing cosine similarity search.
- **Verification**: Search for a concept (e.g. "flight ticket for conference") and verify retrieval of relevant email even without exact keyword match.

### Phase 4.2: Inbox Intelligence & Digest API
- [ ] Build REST endpoints in `app/api/digest.py`:
  - `GET /api/v1/digest/today`: Returns summary of today's incoming emails grouped by urgency and category.
  - `GET /api/v1/action-items`: Returns pending action items with upcoming deadlines.
  - `GET /api/v1/threads/{thread_id}/summary`: Returns the executive digest of a specific thread.
- **Verification**: Query `/api/v1/digest/today` via `curl` / OpenAPI UI and confirm formatted response.

### Phase 4.3: Resilience, Rate Limiting & Safety Guardrails
- [ ] Implement exponential backoff for Gmail API quota handling (Google 429/503 responses).
- [ ] Implement fallback behavior when Gemini API is unavailable or rate-limited.
- [ ] Add strict code-level guardrail verifying that no method attempts write/send operations (`read-only assertion`).
- **Verification**: Unit tests simulating API errors verify graceful retries and zero send capability.

### Phase 4.4: Milestone 4 Verification
- **Milestone 4 Test**: End-to-end test querying daily digests and performing semantic search across stored inbox intelligence.

---

## Phase 5: Production Deployment & Containerization

### Phase 5.1: Production Dockerization
- [ ] Write optimized multi-stage `Dockerfile`.
- [ ] Configure `docker-compose.prod.yml` including FastAPI service and PostgreSQL with `pgvector`.
- [ ] Implement `/health` and `/ready` endpoints for container orchestration.
- **Verification**: Stack boots successfully in Docker with healthchecks passing.

### Phase 5.2: Google Cloud Console Setup & Production Deployment Guide
- [ ] Create step-by-step setup guide:
  - Configuring OAuth Consent Screen on Google Cloud Console.
  - Creating OAuth 2.0 Web Client credentials and redirect URIs.
  - Setting up Google Cloud Pub/Sub topic and subscription for Gmail push notifications.
  - Environment variables and secret management.
- **Milestone 5 Test**: Final verification on staging/production environment.
