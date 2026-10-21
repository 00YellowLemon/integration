# Constitution: Technology Stack & Architecture

## 1. Core Stack Overview

| Layer | Technology | Rationale |
|---|---|---|
| **Language & Runtime** | Python 3.11+ | Modern async/await performance, strong typing, best ecosystem for AI/LLM integration. |
| **API Framework** | FastAPI | High-performance async ASGI web framework with native Pydantic v2 data validation and auto-generated OpenAPI docs. |
| **ASGI Server** | Uvicorn | Lightweight, lightning-fast async ASGI web server for production and development. |
| **Messaging Channel** | Meta WhatsApp Business Cloud API | Direct cloud-hosted WhatsApp Business API from Meta (Graph API v19.0+); no on-premise WhatsApp hardware required. |
| **LLM Orchestration** | LangChain (`langchain`, `langchain-google-genai`, `langchain-core`) | Rich primitives for agents, tool calling, chat message history, memory retention, and RAG pipelines. |
| **Foundation Model** | Google Gemini 3.8 Flash | High speed, cost efficiency, large context window, multimodal capability, and native function calling. |
| **Database & Session Storage** | PostgreSQL + `pgvector` | Acid-compliant relational database for session storage, message archives, idempotency locks, and vector embeddings for RAG. |
| **ORM / DB Driver** | SQLAlchemy 2.0 (async) + `asyncpg` | Production-grade asynchronous database layer with connection pooling. |
| **HTTP Client** | `httpx` | Fully async HTTP client for outbound Meta Graph API requests. |
| **Configuration** | `pydantic-settings` + `.env` | Type-safe environment variable parsing with validation and secret masking. |
| **Local Tunneling** | ngrok / Cloudflare Tunnel | Exposes local development server via public HTTPS URL for Meta Webhook delivery. |
| **Containerization** | Docker & Docker Compose | Repeatable, isolated environments for app service and PostgreSQL database. |

---

## 2. Architectural Blueprint

```
                     ┌────────────────────────────────────────┐
                     │            WhatsApp User               │
                     └───────────────────▲────────────────────┘
                                         │ WhatsApp Messages
                                         ▼
                     ┌────────────────────────────────────────┐
                     │     Meta WhatsApp Cloud Platform       │
                     └───────┬────────────────────────▲───────┘
       Inbound Webhook Events│                        │ Outbound Send API
      (POST /api/v1/webhook) │                        │ (POST /{phone_id}/messages)
                             ▼                        │
         ┌────────────────────────────────────────────┴─────────────┐
         │                  FastAPI Application                     │
         │                                                          │
         │  ┌───────────────────────┐   ┌────────────────────────┐  │
         │  │   Webhook Router      │   │ WhatsApp Outbound Svc  │  │
         │  │ - Signature check     │   │ - Text & quick replies │  │
         │  │ - Hub verification    │   │ - Media & read receipts│  │
         │  │ - Event parsing       │   └────────────────────────┘  │
         │  └───────────┬───────────┘                ▲              │
         │              │                            │              │
         │              ▼                            │              │
         │  ┌────────────────────────────────────────┴───────────┐  │
         │  │               Agent Orchestrator                   │  │
         │  │   (LangChain Agent + Gemini 3.8 Flash Engine)      │  │
         │  └───────────┬────────────────────────────┬───────────┘  │
         │              │ Context                    │ Tool Calls   │
         │              ▼                            ▼              │
         │  ┌───────────────────────┐   ┌────────────────────────┐  │
         │  │  Postgres Chat Memory │   │ Custom Agent Tools     │  │
         │  │  - Session history    │   │ - Document RAG         │  │
         │  │  - Deduplication log  │   │ - Calendar / Notes     │  │
         │  │  - User state & prefs │   │ - Human Handoff flag   │  │
         │  └───────────┬───────────┘   └────────────────────────┘  │
         └──────────────┼───────────────────────────────────────────┘
                        ▼
         ┌────────────────────────────────────────┐
         │       PostgreSQL Database              │
         │   (Relational Data + pgvector RAG)     │
         └────────────────────────────────────────┘
```

---

## 3. Data Storage & Schema Plan (PostgreSQL)

1. **`contacts`**:
   - `wa_id` (VARCHAR, Primary Key - phone number in E.164 format)
   - `profile_name` (VARCHAR)
   - `first_seen_at` (TIMESTAMP)
   - `last_seen_at` (TIMESTAMP)
   - `is_muted` (BOOLEAN - for human handoff state)
   - `metadata` (JSONB - user preferences, tags, summaries)

2. **`messages`**:
   - `id` (UUID, Primary Key)
   - `wamid` (VARCHAR, Unique - Meta message ID for idempotency deduplication)
   - `wa_id` (VARCHAR, Foreign Key -> `contacts.wa_id`)
   - `direction` (VARCHAR: `inbound` | `outbound`)
   - `message_type` (VARCHAR: `text`, `image`, `audio`, etc.)
   - `body` (TEXT)
   - `raw_payload` (JSONB)
   - `created_at` (TIMESTAMP)

3. **`chat_sessions` / `langchain_history`**:
   - LangChain Postgres Chat Message History table storing turn-by-turn messages per `wa_id`.

4. **`documents` / `embeddings`** (RAG Knowledge Base):
   - `id` (UUID, Primary Key)
   - `title` (VARCHAR)
   - `content` (TEXT)
   - `embedding` (VECTOR(768) / compatible Gemini embedding dimension)
   - `metadata` (JSONB)

---

## 4. Key Security & Operational Standards
- **Webhook Signature Verification**: Verify HMAC SHA256 signature in `X-Hub-Signature-256` header against the Meta App Secret.
- **Asynchronous Task Processing**: Webhooks return an immediate HTTP 200 response to Meta within milliseconds, offloading LLM processing asynchronously to prevent timeout retries from Meta.
- **Strict Typing**: All Meta webhook JSON payloads defined as Pydantic models for compile-time safety and automatic parsing.
- **Environment Isolation**: No hardcoded API keys, tokens, or database URIs.
