# Constitution: Technology Stack & Architecture

## 1. Core Stack Overview

| Layer | Technology | Rationale |
|---|---|---|
| **Language & Runtime** | Python 3.11+ | Modern async/await syntax, strong typing, mature ecosystem for Google APIs and LLM integration. |
| **API Framework** | FastAPI | High-performance async ASGI web framework with native Pydantic v2 data validation and auto-generated OpenAPI documentation. |
| **ASGI Server** | Uvicorn | Lightweight, lightning-fast async ASGI web server for production and development. |
| **Email Integration** | Google Gmail REST API v1 | Official Google API for managing Gmail messages, threads, labels, and history. |
| **Authentication** | Google OAuth 2.0 (`google-auth`, `google-auth-oauthlib`) | Secure User Authorization Code flow for personal `@gmail.com` accounts with offline refresh token support. |
| **Permissions Scope** | `gmail.readonly` | Principle of least privilege: strictly read-only access with zero capability to send, alter, or delete emails. |
| **Real-time Notifications** | Google Cloud Pub/Sub + Webhook | Real-time push notifications when mailbox changes occur (`users.watch`), triggering delta sync. |
| **LLM Orchestration** | LangChain (`langchain`, `langchain-google-genai`, `langchain-core`) | Primitives for structured JSON output, prompt templates, and semantic embeddings. |
| **Foundation Model** | Google Gemini 3.8 Flash | High speed, large context window (1M tokens), cost efficiency, and structured output parsing. |
| **Database & Vector Storage** | PostgreSQL 16 + `pgvector` | ACID-compliant relational storage for tokens, threads, messages, and vector embeddings for semantic search. |
| **ORM / DB Driver** | SQLAlchemy 2.0 (async) + `asyncpg` | High-throughput asynchronous database layer with connection pooling. |
| **HTTP Client** | `httpx` | Fully async HTTP client for external network calls and webhook handlers. |
| **Configuration** | `pydantic-settings` + `.env` | Type-safe environment variable parsing with validation and secret masking. |
| **Local Tunneling** | ngrok / Cloudflare Tunnel | Exposes local development server for receiving Google Cloud Pub/Sub push webhooks. |
| **Containerization** | Docker & Docker Compose | Containerized application and database deployment with isolated environment variables. |

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
          │  │          Gemini 3.8 Flash Intelligence Engine       │  │
          │  │ - Categorization & Triage (Urgent, Actions, etc.)  │  │
          │  │ - Action Item & Deadline Extraction                │  │
          │  │ - Thread Synthesis & Executive Briefings           │  │
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
                         ▼
          ┌────────────────────────────────────────┐
          │          PostgreSQL Database           │
          │  - accounts, threads, messages         │
          │  - email_analyses, action_items        │
          │  - pgvector semantic embeddings        │
          └────────────────────────────────────────┘
```

---

## 3. Data Storage & Schema Plan (PostgreSQL)

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
- `summary` (TEXT - generated by Gemini)

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

### 4. `email_analyses`
Structured output generated by Gemini 3.8 Flash:
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

## 4. Key Security & Operational Standards

1. **Strict Read-Only Guarantee**:
   - The application only requests `https://www.googleapis.com/auth/gmail.readonly`.
   - The service layer contains zero methods for `users.messages.send`, `users.drafts.create`, or `users.messages.delete`.
2. **Encrypted Token Storage**:
   - Refresh tokens and access tokens are encrypted at rest using AES-GCM via an application secret key.
3. **Idempotency & Delta Synchronization**:
   - Webhook retries and sync runs are deduplicated by `gmail_message_id`.
   - Mailbox synchronization uses Gmail's `historyId` API to pull only incremental changes, avoiding full mailbox scans and conserving API quota.
4. **Resilience & Backoff**:
   - Google API calls wrap with exponential backoff on HTTP 429 and 503 errors.
   - LLM generation failures fail safely without dropping the ingested message.
5. **Data Hygiene & Privacy**:
   - Raw HTML is sanitized to prevent XSS.
   - Attachments are processed for metadata only; raw binary content is not retained in database memory unless explicitly configured.
