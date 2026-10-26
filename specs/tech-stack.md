# Constitution: Technology Stack & Architecture

## 1. Core Stack Overview

| Layer | Technology | Rationale |
|---|---|---|
| **Language & Runtime** | Python 3.11+ | Modern async/await syntax, strong typing, mature ecosystem for Google APIs and LangChain agent integration. |
| **API Framework** | FastAPI | High-performance async ASGI web framework with native Pydantic v2 data validation and auto-generated OpenAPI documentation. |
| **ASGI Server** | Uvicorn | Lightweight, lightning-fast async ASGI web server; on Cloud Run it binds `0.0.0.0:$PORT` via the `Procfile`. |
| **Email Integration** | Google Gmail REST API v1 | Official Google API for managing Gmail messages, threads, labels, and history. |
| **Authentication** | Google OAuth 2.0 (`google-auth`, `google-auth-oauthlib`) | Secure User Authorization Code flow for personal `@gmail.com` accounts with offline refresh token support. |
| **Permissions Scope** | `gmail.readonly` | Principle of least privilege: strictly read-only access with zero capability to send, alter, or delete emails. |
| **Real-time Notifications** | Google Cloud Pub/Sub + Webhook | Real-time push notifications when mailbox changes occur (`users.watch`), triggering delta sync. |
| **Agent Framework** | LangChain (`create_agent`, `langchain.tools`) | High-level agent orchestration with declarative tool calling, typed state management, and middleware support. |
| **Agent Short-Term Memory** | `langgraph-checkpoint-postgres` (`PostgresSaver`) | Thread-level conversational state and execution checkpoints stored directly in PostgreSQL using the provided connection string. |
| **Foundation Model** | Google Gemini 3.8 Flash (`langchain-google-genai`) | High speed, 1M context window, cost efficiency, and structured tool-calling capabilities. |
| **Database & Vector Storage** | PostgreSQL 16 + `pgvector` | Unified ACID storage for relational email data, LangChain memory checkpointer, and vector embeddings for semantic search. |
| **Database Access & ORM** | SQLAlchemy 2.0 (async) + `asyncpg` + `psycopg[binary]` | Asynchronous database access for FastAPI; `psycopg[binary]` for `langgraph-checkpoint-postgres`. |
| **Frontend DB Integration** | Direct PostgreSQL Connection String | Frontend dashboard connects directly to PostgreSQL using the provided connection string (supplemented by FastAPI REST endpoints). |
| **HTTP Client** | `httpx` | Fully async HTTP client for external network calls and webhook handlers. |
| **Configuration** | `pydantic-settings` + `.env` | Type-safe environment variable parsing with validation and secret masking. |
| **Production Hosting** | Google Cloud Run (source deploy) | Fully managed, stateless container runtime with scale-to-zero, an HTTPS URL, revisioned rollbacks, and native Secret Manager and Pub/Sub push integration. |
| **Process Definition** | `Procfile` (`web:` process) | Declares the production start command consumed by Google Cloud buildpacks: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. No Dockerfile or Compose file is required. |
| **Build Pipeline** | Google Cloud Build + Cloud Run source deploy | `gcloud run deploy --source .` uses Python buildpacks, pushes the image to Artifact Registry, and rolls out a new revision in a single command. |
| **Secret Management** | Google Secret Manager (`--set-secrets`) | `GOOGLE_CLIENT_SECRET`, `APP_SECRET_KEY`, and `DATABASE_URL` are injected at runtime and never baked into an image or committed to the repository. |
| **Runtime Identity** | Dedicated Cloud Run service account + ADC | The service runs as `gmail-triage-runner@<PROJECT_ID>.iam.gserviceaccount.com`; Application Default Credentials authorize Gmail, Pub/Sub, and Secret Manager without key files. |
| **Local Tunneling (dev only)** | ngrok / Cloudflare Tunnel | Exposes the local development server for Pub/Sub push testing; production Pub/Sub pushes directly to the Cloud Run HTTPS URL. |

---

## 2. Architectural Blueprint

```
                      ┌────────────────────────────────────────┐
                      │          Personal Gmail User           │
                      │         (Google OAuth 2.0)             │
                      └───────────────────▲────────────────────┘
                                          │ 1. Authorize (Read-Only)
                                          ▼
                      ┌────────────────────────────────────────┐
                      │           Google APIs Cloud            │
                      │  - OAuth 2.0 Token Server              │
                      │  - Gmail API v1 (users.messages/history)│
                      │  - Google Cloud Pub/Sub Push Topic     │
                      └───────┬────────────────────────▲───────┘
          Pub/Sub Push Webhook│                        │ Read-Only REST Requests
   (POST /api/v1/webhook)     │                        │ (GET /users/me/...)
                              ▼                        │
          ┌────────────────────────────────────────────┴─────────────┐
          │                  FastAPI Application                     │
          │                                                          │
          │  ┌───────────────────────┐   ┌────────────────────────┐  │
          │  │   Auth & Ingestion    │   │  MIME / Content Parser │  │
          │  │ - OAuth callback      │   │ - RFC 2822 parsing     │  │
          │  │ - Token auto-refresh  │   │ - HTML-to-text decode  │  │
          │  │ - Pub/Sub push verify │   │ - Attachment metadata  │  │
          │  │ - Delta sync (history)│   └────────────────────────┘  │
          │  └───────────┬───────────┘                ▲              │
          │              │                            │              │
          │              ▼                            │              │
          │  ┌────────────────────────────────────────┴───────────┐  │
          │  │        LangChain Email Triage Tool Agent           │  │
          │  │      (create_agent + Gemini 3.8 Flash)             │  │
          │  │                                                    │  │
          │  │  Agent Tools:                                      │  │
          │  │  - inspect_email_payload()                         │  │
          │  │  - classify_urgency_and_category()                 │  │
          │  │  - extract_action_items_and_deadlines()            │  │
          │  │  - synthesize_thread_summary()                     │  │
          │  │                                                    │  │
          │  │  Short-Term Memory:                                │  │
          │  │  - PostgresSaver checkpointer (thread_id state)    │  │
          │  └───────────┬────────────────────────────┬───────────┘  │
          │              │ Digest / Query API         │ Embeddings   │
          │              ▼                            ▼              │
          │  ┌───────────────────────┐   ┌────────────────────────┐  │
          │  │  Query & Digest API   │   │  Read-Only Guardrail   │  │
          │  │ - GET /digest/today   │   │ - Zero send methods    │  │
          │  │ - GET /action-items   │   │ - gmail.readonly scope │  │
          │  │ - Semantic Search API │   │ - Safe sandbox design  │  │
          │  └───────────┬───────────┘   └────────────────────────┘  │
          └──────────────┼───────────────────────────────────────────┘
                         │
                         ▼
          ┌──────────────────────────────────────────────────────────┐
          │                   PostgreSQL Database                    │
          │  - accounts, threads, messages                           │
          │  - email_analyses, action_items                          │
          │  - pgvector semantic embeddings                          │
          │  - LangGraph checkpointer tables:                        │
          │      checkpoints, checkpoint_blobs, checkpoint_writes    │
          └───────────────────────────▲──────────────────────────────┘
                                      │
                                      │ Direct Connection String
                                      │
                         ┌────────────┴─────────────┐
                         │    Frontend Dashboard    │
                         │ (Direct Postgres Access) │
                         └──────────────────────────┘
```

> **Runtime note:** the entire FastAPI application block above is deployed as one stateless **Google Cloud Run** service. Cloud Run injects `$PORT`; the repository `Procfile` web process binds `0.0.0.0:$PORT`. There is no Dockerfile, Docker Compose stack, or self-managed reverse proxy in the production topology.

---

## 3. LangChain Triage Agent Architecture & Short-Term Memory

### 3.1 Agent Definition
The triage agent is initialized via LangChain's `create_agent` primitive:
- **Model**: `google_genai:gemini-3.8-flash` initialized with temperature 0.1 for high determinism.
- **Tools**:
  - `inspect_email_payload`: Loads parsed message body, sender history, and headers.
  - `classify_urgency_and_category`: Evaluates content against triage categories and urgency rubric (1-5).
  - `extract_action_items_and_deadlines`: Extracts actionable commitments, assignees, and deadlines.
  - `synthesize_thread_summary`: Generates concise executive summaries for multi-turn threads.
- **Short-Term Memory Checkpointer**: `PostgresSaver` from `langgraph-checkpoint-postgres` connects using `DATABASE_URL` (connection string) to maintain conversational state and thread checkpoints across agent execution turns.

### 3.2 Short-Term Memory Checkpoint Schema
`PostgresSaver.setup()` creates and manages the following tables:
- `checkpoints`: Thread checkpoint metadata, turn identifiers, and timestamps.
- `checkpoint_blobs`: Serialized state data, messages, and intermediate tool results.
- `checkpoint_writes`: Pending and committed state channel writes per turn.

---

## 4. Data Storage & Schema Plan (PostgreSQL)

### 1. `accounts`
Stores OAuth 2.0 credentials and mailbox sync cursors:
- `id` (UUID, Primary Key)
- `email` (VARCHAR, Unique - e.g. `user@gmail.com`)
- `access_token` (TEXT - encrypted)
- `refresh_token` (TEXT - encrypted)
- `token_expiry` (TIMESTAMP)
- `history_id` (VARCHAR - current Gmail sync checkpoint)
- `is_active` (BOOLEAN)
- `created_at` (TIMESTAMP)
- `updated_at` (TIMESTAMP)

### 2. `threads`
Tracks Gmail conversation threads:
- `id` (UUID, Primary Key)
- `gmail_thread_id` (VARCHAR, Unique - Gmail thread ID)
- `account_id` (UUID, Foreign Key -> `accounts.id`)
- `subject` (VARCHAR)
- `snippet` (TEXT)
- `history_id` (VARCHAR)
- `last_message_at` (TIMESTAMP)
- `summary` (TEXT - generated by Gemini agent)

### 3. `messages`
Stores ingested individual email messages:
- `id` (UUID, Primary Key)
- `gmail_message_id` (VARCHAR, Unique - Gmail message ID for idempotency)
- `thread_id` (UUID, Foreign Key -> `threads.id`)
- `sender` (VARCHAR)
- `recipient` (VARCHAR)
- `subject` (VARCHAR)
- `date` (TIMESTAMP)
- `snippet` (TEXT)
- `body_text` (TEXT)
- `body_html` (TEXT, Nullable)
- `has_attachments` (BOOLEAN)
- `attachment_names` (JSONB)
- `raw_headers` (JSONB)
- `created_at` (TIMESTAMP)

### 4. `email_analyses` (Triage Results)
Structured output generated by the LangChain Triage Agent:
- `id` (UUID, Primary Key)
- `message_id` (UUID, Foreign Key -> `messages.id`)
- `category` (VARCHAR - e.g. `Urgent`, `Action Required`, `Invoice`, `Newsletter`, `General`)
- `urgency_score` (INTEGER - 1 to 5)
- `summary` (TEXT)
- `confidence` (FLOAT)
- `embedding` (VECTOR(768) - pgvector for semantic search)
- `created_at` (TIMESTAMP)

### 5. `action_items`
Discrete tasks extracted from emails:
- `id` (UUID, Primary Key)
- `analysis_id` (UUID, Foreign Key -> `email_analyses.id`)
- `message_id` (UUID, Foreign Key -> `messages.id`)
- `description` (TEXT - the action required)
- `deadline` (TIMESTAMP, Nullable)
- `assignee` (VARCHAR, Nullable)
- `status` (VARCHAR - `pending`, `dismissed`, `done`)
- `created_at` (TIMESTAMP)

---

## 5. Key Security & Operational Standards

1. **Strict Read-Only Guarantee**:
   - The application only requests `https://www.googleapis.com/auth/gmail.readonly`.
   - The service layer contains zero methods for `users.messages.send`, `users.drafts.create`, or `users.messages.delete`.
2. **Encrypted Token Storage**:
   - Refresh tokens and access tokens are encrypted at rest using AES-GCM via an application secret key.
3. **Idempotency & Delta Synchronization**:
   - Webhook retries and sync runs are deduplicated by `gmail_message_id`.
   - Mailbox synchronization uses Gmail's `historyId` API to pull only incremental changes, avoiding full mailbox scans and conserving API quota.
4. **Resilience & State Recovery**:
   - Google API calls wrap with exponential backoff on HTTP 429 and 503 errors.
   - LangChain checkpointer in PostgreSQL enables the triage agent to resume interrupted runs without lost state.
5. **Data Hygiene & Privacy**:
   - Raw HTML is sanitized to prevent XSS.
   - Attachments are processed for metadata only; raw binary content is not retained in database memory.
6. **Cloud Hosting Security**:
   - Runtime secrets are stored in Google Secret Manager and injected by Cloud Run; no secrets live in the image, repository, or `Procfile`.
   - The service runs under a dedicated least-privilege service account rather than the default compute identity.
   - Pub/Sub push requests are authenticated with an OIDC token and validated (issuer + audience) before ingestion.

---

## 6. Deployment Architecture: Google Cloud Run + Procfile

### 6.1 Runtime Contract
Production is a single stateless Cloud Run service built from source. The repository root contains the process definition:

```
web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

- Cloud Run injects `PORT` (default `8080`); the application binds `0.0.0.0` and never hardcodes `8000` in production.
- The container filesystem is ephemeral. All durable state lives in PostgreSQL; nothing relies on local disk persistence.
- Scale-to-zero is enabled (`--min-instances 0`). CPU is throttled outside request handling, so Pub/Sub-triggered ingestion must complete within the request lifecycle or be handed off explicitly.
- `/health` doubles as the startup and liveness probe.

### 6.2 Build & Release
- `gcloud run deploy --source .` triggers Google Cloud Build, which detects Python via buildpacks (no `Dockerfile`) and reads the `Procfile` for the start command.
- Images are stored in Artifact Registry; every deploy creates an immutable revision.
- Rollback and traffic splitting use `gcloud run services update-traffic`.
- A `.gcloudignore` keeps virtualenvs, caches, tests, and `.env` files out of the uploaded source.

### 6.3 Identity, Secrets & Configuration
- The service runs as `gmail-triage-runner@<PROJECT_ID>.iam.gserviceaccount.com` with only the roles it needs: Secret Manager accessor and Pub/Sub publisher.
- Application Default Credentials from that service account authorize Gmail API, Pub/Sub, and Secret Manager calls; no JSON key files are stored anywhere.
- `GOOGLE_CLIENT_SECRET`, `APP_SECRET_KEY`, and `DATABASE_URL` are mounted from Secret Manager via `--set-secrets`.
- Non-sensitive settings (`GOOGLE_CLIENT_ID`, `GOOGLE_REDIRECT_URI`, `GCP_PROJECT_ID`, `PUBSUB_TOPIC_NAME`, `PUBSUB_SUBSCRIPTION_NAME`, `LOG_LEVEL`) are supplied with `--set-env-vars`.
- Pub/Sub push requests carry an OIDC token whose issuer and audience are validated by the webhook handler.

### 6.4 URLs & Integrations
- The public Cloud Run URL is the OAuth base: `https://<SERVICE_URL>/auth/callback` must be registered on the Google Cloud Console OAuth web client.
- The Pub/Sub push subscription targets `https://<SERVICE_URL>/api/v1/webhook/pubsub`.
- The frontend keeps its direct PostgreSQL connection string; only the API moves to Cloud Run.
- Dockerfile, Docker Compose, and self-managed reverse proxies are explicitly out of scope for production.
