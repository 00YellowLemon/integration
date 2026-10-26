# Constitution: Implementation Roadmap

This roadmap breaks down the construction of the **Gmail AI Email Triage Agent** into **micro-phases** of work. Each phase is small, self-contained, and testable before proceeding to the next.

---

## Phase 1: Environment Scaffolding & Google OAuth 2.0 Integration

### Phase 1.1: Project Skeleton & Configuration Management
- [x] Initialize Python project structure (`app/api/`, `app/core/`, `app/services/`, `app/models/`, `specs/`, `tests/`).
- [x] Setup `requirements.txt` / `pyproject.toml` with dependencies: `fastapi`, `uvicorn`, `pydantic`, `pydantic-settings`, `httpx`, `google-auth-oauthlib`, `google-api-python-client`.
- [x] Implement `app/core/config.py` using `pydantic-settings` to load Google credentials (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `APP_SECRET_KEY`, `DATABASE_URL`).
- [x] Create `.env.example` template with step-by-step setup instructions.
- **Verification**: App boots with valid configs and fails gracefully when required environment variables are missing.

### Phase 1.2: Google OAuth 2.0 Authorization Flow
- [x] Create FastAPI authentication router in `app/api/auth.py`.
- [x] Implement `GET /auth/login` to generate Google OAuth consent URL with:
  - Scopes: `https://www.googleapis.com/auth/gmail.readonly`, `https://www.googleapis.com/auth/userinfo.email`.
  - Parameters: `access_type="offline"`, `prompt="consent"`, secure CSRF `state`.
- [x] Implement `GET /auth/callback` to handle Google redirect:
  - Verify CSRF `state`.
  - Exchange authorization code for access and refresh tokens.
- **Verification**: Access `/auth/login` in browser, authorize with personal Gmail account, and confirm code exchange yields valid tokens.

### Phase 1.3: Token Persistence & Auto-Refresh Mechanism
- [x] Build token storage service to manage access and refresh tokens.
- [x] Implement automatic refresh logic when access token expires or is near expiry (using `google.auth.transport.requests`).
- [x] Provide utility function to return authenticated Google credentials for API calls.
- **Verification**: Run unit test with an expired access token and verify that the refresh token successfully fetches a new access token without re-prompting the user.

### Phase 1.4: Gmail API Client & Connection Verification
- [x] Build async-compatible `GmailClient` in `app/services/gmail.py`.
- [x] Implement profile handshake method:
  - `get_profile()` calling Gmail API `users.getProfile(userId='me')`.
- [x] Create `GET /api/v1/gmail/status` endpoint returning connected email address, total messages, and thread count.
- **Verification**: Calling `/api/v1/gmail/status` returns HTTP 200 with accurate user profile stats for the connected Gmail account.

### Phase 1.5: Milestone 1 End-to-End Connection Loop
- [x] Create unified startup health check and token validation.
- **Milestone 1 Test**: Authenticate via OAuth 2.0 flow and successfully fetch profile details from Gmail API.

---

## Phase 2: PostgreSQL Persistence & Email Ingestion Pipeline

### Phase 2.1: PostgreSQL & Async Engine Setup
- [x] Provision a managed PostgreSQL 16 database with the `pgvector` extension (Cloud SQL or an external provider connection string); no local container runtime is required.
- [x] Setup SQLAlchemy 2.0 async engine and session factory (`asyncpg`) using the configured `DATABASE_URL`.
- [x] Verify frontend database connectivity with the provided connection string.
- [x] Create base database configuration and initialization lifecycle in FastAPI.
- **Verification**: Database container launches, connects via async session, and executes `SELECT 1`.

### Phase 2.2: Relational Schema & Idempotent Ingestion
- [x] Define SQLAlchemy models in `app/models/`:
  - `Account` (`id`, `email`, `access_token`, `refresh_token`, `token_expiry`, `history_id`, `created_at`)
  - `Thread` (`id`, `gmail_thread_id`, `snippet`, `history_id`, `last_message_at`)
  - `Message` (`id`, `gmail_message_id`, `thread_id`, `sender`, `recipient`, `subject`, `date`, `snippet`, `body_text`, `body_html`, `has_attachments`)
- [x] Implement idempotent message insertion using unique constraint on `gmail_message_id`.
- **Verification**: Attempting to insert the same Gmail message ID twice updates or ignores safely without duplicate rows or errors.

### Phase 2.3: MIME Parser & Content Sanitization
- [x] Build MIME message parser in `app/services/mime_parser.py`:
  - Decode standard multi-part emails (plain text vs. HTML).
  - Strip dangerous script tags and convert HTML to readable markdown/plain text.
  - Extract attachment metadata (filename, mime type, size in bytes) without saving huge payloads.
- **Verification**: Test with diverse email structures (plain text, multipart/alternative, multipart/mixed) and verify clean body extraction.

### Phase 2.4: Real-time Ingestion via Google Cloud Pub/Sub & Sync Cursor
- [x] Implement `users.watch` registration utility for Gmail mailbox push notifications.
- [x] Create `POST /api/v1/webhook/pubsub` endpoint to receive Google Cloud Pub/Sub push notifications.
- [x] Implement delta sync worker using Gmail `users.history.list` starting from stored `history_id`.
- [x] Provide fallback polling endpoint `POST /api/v1/gmail/sync` for manual or cron-based delta sync.
- **Verification**: Trigger a manual sync or send a test email to the account, confirming new messages are fetched and stored in PostgreSQL.

### Phase 2.5: Milestone 2 Ingestion Verification
- **Milestone 2 Test**: Connect account, run sync, and verify that recent Gmail threads and messages are populated accurately in PostgreSQL.

---

## Phase 3: LangChain Email Triage Agent & Postgres Short-Term Memory

### Phase 3.1: LangChain & Gemini 3.8 Flash Agent Harness Setup
- [x] Add `langchain`, `langchain-google-genai`, and `langgraph-checkpoint-postgres` to dependencies.
- [x] Configure `ChatGoogleGenerativeAI(model="gemini-3.8-flash")` with low temperature (0.1) for deterministic analysis.
- [x] Define Pydantic triage schemas (`EmailAnalysisResult`, `ActionItemResult`, `CategoryEnum`).
- **Verification**: Standalone script runs Gemini 3.8 Flash via LangChain and parses a mock email into structured schema.

### Phase 3.2: PostgreSQL Short-Term Memory Checkpointer
- [x] Integrate `langgraph-checkpoint-postgres` (`PostgresSaver`) using the provided PostgreSQL connection string.
- [x] Initialize checkpointer tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`) via `checkpointer.setup()`.
- [x] Verify thread-scoped memory resumption using `configurable: {"thread_id": ...}`.
- **Verification**: Agent retains conversation and triage state across successive `invoke` calls on the same thread ID.

### Phase 3.3: Triage Agent Tools Implementation
- [x] Implement `@tool` functions for the triage agent:
  - `inspect_email_payload(message_id: str)`: Fetches body text, sender history, and headers.
  - `classify_urgency_and_category(message_id: str, category: str, urgency_score: int, rationale: str)`: Records triage classifications.
  - `extract_action_items_and_deadlines(message_id: str, items: list)`: Records identified action items and due dates.
  - `synthesize_thread_summary(thread_id: str, summary: str)`: Generates multi-turn thread executive briefings.
- **Verification**: Unit tests verify each tool validates inputs against Pydantic models and records outputs properly.

### Phase 3.4: Autonomous Triage Agent Assembly (`create_agent`)
- [x] Assemble agent using LangChain's `create_agent(model=..., tools=[...], checkpointer=checkpointer)`.
- [x] Author structured system prompt defining triage rubrics, urgency criteria (1-5), and strict read-only behavior.
- [x] Implement triage background worker triggered after email ingestion.
- **Verification**: Process an incoming email through the agent; confirm tools are invoked and results are written to `email_analyses` and `action_items` in PostgreSQL.

### Phase 3.5: Multi-Turn Thread Synthesis & Summarization
- [x] Implement thread-level aggregation logic combining sequential messages into chronological digests.
- [x] Prompt the agent with thread context to synthesize decisions made, pending items, and conversation summaries.
- [x] Save executive summaries to the `threads` table.
- **Verification**: Run triage agent on a 5-message conversation thread and verify concise, accurate executive briefing.

### Phase 3.6: Milestone 3 Triage Agent Verification Test
- **Milestone 3 Test**: Ingest a new email thread, run the LangChain Triage Agent with PostgreSQL short-term memory, and verify structured triage categories, action items, and thread summary stored in PostgreSQL.

---

## Phase 4: Frontend Data Views, Digest APIs & Semantic Search

### Phase 4.1: Vector Embeddings & Semantic Search
- [ ] *(Deferred per user directive)* Add `embedding` column (`VECTOR(768)`) to `messages` or `email_analyses` using `pgvector`.
- [ ] *(Deferred per user directive)* Integrate Gemini embedding model to generate embeddings for processed email contents and summaries.
- [ ] *(Deferred per user directive)* Implement `search_emails(query: str, limit: int)` performing cosine similarity search.
- **Verification**: Deferred; pure triage intelligence prioritized.

### Phase 4.2: Frontend-Facing Postgres Queries & Triage APIs
- [x] Optimize database indexes and views for direct frontend PostgreSQL querying (urgent triage feed, action items list, thread summaries).
- [x] Build FastAPI REST endpoints in `app/api/digest.py`:
  - `GET /api/v1/digest/today`: Returns summary of today's incoming emails grouped by urgency and category.
  - `GET /api/v1/action-items`: Returns pending action items with upcoming deadlines.
  - `GET /api/v1/threads/{thread_id}/triage`: Returns the executive triage briefing for a thread.
- **Verification**: Direct query via SQL client and FastAPI endpoint returns matching formatted triage feeds.

### Phase 4.3: Resilience, Rate Limiting & Safety Guardrails
- [x] Implement exponential backoff for Gmail API quota handling (Google 429/503 responses).
- [x] Implement fallback behavior when Gemini API is unavailable or rate-limited.
- [x] Add strict code-level guardrail verifying zero write/send capability in the service layer (`read-only assertion`).
- **Verification**: Simulate API failure and verify graceful retries without crashing or corrupting PostgreSQL state.

### Phase 4.4: Milestone 4 Verification Test
- [x] **Milestone 4 Test**: End-to-end test querying daily digests, verifying action items, and performing verified triage across stored inbox intelligence.

---

## Phase 5: Google Cloud Run Deployment (Procfile, No Docker)

Deployment happens entirely from the terminal with the already-authenticated `gcloud` CLI. There is no Dockerfile and no Docker Compose: Cloud Run builds the source with Google Cloud buildpacks and starts the app from the repository `Procfile`.

### Phase 5.1: Procfile & Cloud Run Runtime Contract
- [ ] Add `Procfile` at the repository root: `web: uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- [ ] Read the injected `PORT` environment variable instead of hardcoding `8000`, and always bind `0.0.0.0`.
- [ ] Add `.gcloudignore` excluding `.venv/`, `__pycache__/`, `.pytest_cache/`, `tests/`, `.env`, and `*.pyc`.
- [ ] Confirm the app is stateless: durable state only in PostgreSQL, no writes to the local filesystem.
- **Verification**: Boot locally with `PORT=8080` set; `/health` responds on 8080 with no container runtime involved.

### Phase 5.2: GCP Project, APIs & Service Account
- [ ] `gcloud config set project <PROJECT_ID>` and confirm `gcloud auth list` shows the active account.
- [ ] Enable APIs: `run`, `cloudbuild`, `artifactregistry`, `secretmanager`, `pubsub`.
- [ ] `gcloud iam service-accounts create gmail-triage-runner --display-name="Gmail Triage Cloud Run"`.
- [ ] Grant `roles/secretmanager.secretAccessor` and `roles/pubsub.publisher` to the service account.
- **Verification**: `gcloud iam service-accounts describe gmail-triage-runner@<PROJECT_ID>.iam.gserviceaccount.com` returns the account.

### Phase 5.3: Secrets in Secret Manager
- [ ] Create secrets `GOOGLE_CLIENT_SECRET`, `APP_SECRET_KEY`, and `DATABASE_URL`.
- [ ] Add current values via `gcloud secrets versions add <SECRET> --data-file=-`.
- [ ] Keep non-secret settings (`GOOGLE_CLIENT_ID`, `GOOGLE_REDIRECT_URI`, `GCP_PROJECT_ID`, `PUBSUB_TOPIC_NAME`, `PUBSUB_SUBSCRIPTION_NAME`, `LOG_LEVEL`) as plain env vars.
- **Verification**: `gcloud secrets versions access latest --secret=DATABASE_URL` returns the connection string without it ever entering the repository.

### Phase 5.4: First Source Deploy to Cloud Run
- [ ] Deploy from source without a Dockerfile:
  ```
  gcloud run deploy gmail-triage --source . --region <REGION> \
    --service-account gmail-triage-runner@<PROJECT_ID>.iam.gserviceaccount.com \
    --allow-unauthenticated --port 8080 --min-instances 0 --max-instances 2 --timeout 300 \
    --set-secrets GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:latest,APP_SECRET_KEY=APP_SECRET_KEY:latest,DATABASE_URL=DATABASE_URL:latest \
    --set-env-vars GOOGLE_CLIENT_ID=<ID>,GCP_PROJECT_ID=<PROJECT_ID>,PUBSUB_TOPIC_NAME=gmail-notifications,PUBSUB_SUBSCRIPTION_NAME=gmail-notifications-sub
  ```
- [ ] Confirm buildpacks detect Python, the `Procfile` start command runs, and startup database init succeeds in the logs.
- **Verification**: `gcloud run services describe gmail-triage --format='value(status.url)'` returns a URL and `curl <SERVICE_URL>/health` returns `{"status":"ok"}`.

### Phase 5.5: OAuth Redirect URI Cutover
- [ ] In Google Cloud Console → APIs & Services → Credentials, add `https://<SERVICE_URL>/auth/callback` to Authorized redirect URIs (console-only; not scriptable via `gcloud`).
- [ ] `gcloud run services update gmail-triage --update-env-vars GOOGLE_REDIRECT_URI=https://<SERVICE_URL>/auth/callback`.
- **Verification**: Visit `https://<SERVICE_URL>/auth/login`, complete consent, and confirm tokens are stored in PostgreSQL.

### Phase 5.6: Pub/Sub Push Wiring to Cloud Run
- [ ] Re-register `users.watch` against `projects/<PROJECT_ID>/topics/gmail-notifications`.
- [ ] Create or update the push subscription targeting `https://<SERVICE_URL>/api/v1/webhook/pubsub` with `--push-auth-service-account`.
- **Note**: the service stays `--allow-unauthenticated` for the browser OAuth flow, so `roles/run.invoker` is not required for Pub/Sub; the webhook enforces OIDC at the application layer.
- [ ] Validate the OIDC JWT in `app/api/webhook.py` (issuer and audience) and reject unsigned requests.
- **Verification**: Send a test email; Cloud Run logs show the webhook firing and exactly one new message row in PostgreSQL.

### Phase 5.7: Production Smoke Test, Observability & Rollback
- [ ] Tail logs with `gcloud run services logs tail gmail-triage` and confirm clean startup plus database initialization.
- [ ] Verify `/health` reports `"database":"connected"`.
- [ ] Rehearse rollback: `gcloud run services update-traffic gmail-triage --to-revisions <PREVIOUS_REVISION>=100`.
- [ ] Record deploy, secret-rotation, and rollback commands in the project runbook.
- **Milestone 5 Test**: From a clean terminal, deploy a revision with `gcloud run deploy --source .`, complete OAuth against the Cloud Run URL, receive a real Pub/Sub notification, and verify triage output in PostgreSQL.
