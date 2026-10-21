# Constitution: Implementation Roadmap

This roadmap breaks down the construction of the WhatsApp AI Personal Assistant into **micro-phases** of work. Each phase is small, self-contained, and testable before proceeding to the next.

---

## Phase 1: Environment Scaffolding & WhatsApp I/O

### Phase 1.1: Project Skeleton & Configuration Management
- [ ] Initialize Python project structure (`app/`, `specs/`, `tests/`).
- [ ] Setup `requirements.txt` / `pyproject.toml` with dependencies: `fastapi`, `uvicorn`, `pydantic`, `pydantic-settings`, `httpx`.
- [ ] Implement `app/config.py` using `pydantic-settings` to load Meta credentials (`META_ACCESS_TOKEN`, `META_PHONE_NUMBER_ID`, `META_VERIFY_TOKEN`, `META_APP_SECRET`).
- [ ] Create `.env.example` template with clear instructions.
- **Verification**: App boots with valid configs and fails gracefully when required environment variables are missing.

### Phase 1.2: Meta Webhook Verification Handshake
- [ ] Create FastAPI app in `app/main.py`.
- [ ] Implement `GET /api/v1/webhook` endpoint adhering to Meta's verification challenge protocol:
  - Validate `hub.mode == "subscribe"`.
  - Validate `hub.verify_token == META_VERIFY_TOKEN`.
  - Return integer value of `hub.challenge` as plain text.
- **Verification**: Test with `curl` and verify handshake responds with HTTP 200 and challenge string.

### Phase 1.3: Webhook Event Ingestion & Signature Verification
- [ ] Implement HMAC-SHA256 signature verification middleware/dependency checking `X-Hub-Signature-256` against `META_APP_SECRET`.
- [ ] Define Pydantic models for Meta webhook payload (entries, changes, value, contacts, messages).
- [ ] Implement `POST /api/v1/webhook` endpoint that validates signature, parses incoming messages, logs payload, and responds immediately with HTTP 200.
- **Verification**: Send simulated webhook payload via `pytest` and verify payload parsing and signature validation.

### Phase 1.4: WhatsApp Outbound Messaging Client
- [ ] Build async `WhatsAppClient` in `app/services/whatsapp.py` using `httpx`.
- [ ] Implement methods:
  - `send_text_message(to: str, text: str)`
  - `mark_as_read(message_id: str)`
- **Verification**: Dispatch a live test text message to a designated phone number via Meta Graph API.

### Phase 1.5: Milestone 1 End-to-End Echo Loop
- [ ] Connect Inbound Webhook (`POST /api/v1/webhook`) to `WhatsAppClient` via a background task.
- [ ] Expose local server via ngrok or Cloudflare tunnel.
- [ ] Configure Webhook URL in Meta App Dashboard.
- **Milestone 1 Test**: Send a WhatsApp message to the business number; bot marks as read and echoes the text back immediately.

---

## Phase 2: PostgreSQL Persistence & Session Memory

### Phase 2.1: PostgreSQL & Async Engine Setup
- [ ] Add `docker-compose.yml` for local PostgreSQL with `pgvector` extension.
- [ ] Setup SQLAlchemy 2.0 async engine and session factory (`asyncpg`).
- [ ] Create base database configuration and initialization lifecycle in FastAPI.
- **Verification**: Database container launches, connects via async session, and executes `SELECT 1`.

### Phase 2.2: Relational Schema & Message Idempotency
- [ ] Define SQLAlchemy models:
  - `Contact` (`wa_id`, `name`, `is_muted`, `metadata`)
  - `Message` (`id`, `wamid`, `wa_id`, `direction`, `type`, `body`, `created_at`)
- [ ] Implement idempotent message insertion using unique constraint on `wamid` to prevent duplicate processing on Meta retries.
- **Verification**: Attempting to insert the same `wamid` twice safely resolves without crashing or duplicate downstream execution.

### Phase 2.3: LangChain Chat Message History Integration
- [ ] Implement Postgres-backed chat history adapter for LangChain (`PostgresChatMessageHistory` or custom async chat history store).
- [ ] Bind conversation sessions uniquely to `wa_id`.
- [ ] Include helper to fetch recent $N$ messages as conversation context.
- **Milestone 2 Test**: Verify inbound and outbound messages are recorded in PostgreSQL and history is reliably retrieved across multiple conversation turns.

---

## Phase 3: Gemini 3.8 Flash & Agentic Engine

### Phase 3.1: Gemini 3.8 Flash Provider Setup
- [ ] Install `langchain-google-genai` and configure API key (`GEMINI_API_KEY`).
- [ ] Initialize `ChatGoogleGenerativeAI(model="gemini-3.8-flash")`.
- [ ] Craft system prompt defining the personal assistant persona, tone, owner boundaries, and instruction hierarchy.
- **Verification**: Run standalone test script verifying Gemini 3.8 Flash answers a prompt adhering to the persona.

### Phase 3.2: Tool Calling Infrastructure
- [ ] Setup LangChain tool registry.
- [ ] Implement initial utility tools:
  - `get_current_datetime`: Provides current local time and date.
  - `get_contact_info`: Returns recorded contact profile and past notes.
  - `save_note`: Saves key user facts or reminders to PostgreSQL.
- **Verification**: Unit tests verifying Gemini selects and invokes tools with correct arguments.

### Phase 3.3: Business Document RAG (Retrieval-Augmented Generation)
- [ ] Implement `Document` table with vector embeddings using `pgvector`.
- [ ] Implement document loader/indexer utility for local markdown/PDF/text files.
- [ ] Build vector search tool `search_knowledge_base(query: str)` using Gemini embeddings.
- **Verification**: Ask a domain-specific question; verify agent retrieves context from pgvector and incorporates it into the response.

### Phase 3.4: Milestone 3 Agent Execution Loop
- [ ] Build end-to-end agent orchestrator combining memory, tools, RAG, and Gemini 3.8 Flash.
- [ ] Wire Webhook background worker:
  `Inbound WhatsApp Message -> Fetch Memory -> Agent Reasoning (RAG/Tools) -> Record Output -> Send WhatsApp Reply`.
- **Milestone 3 Test**: Ask the WhatsApp bot questions requiring document knowledge and tool execution; verify seamless natural replies on WhatsApp.

---

## Phase 4: Human Handoff, Safety Guardrails & Monitoring

### Phase 4.1: Human Handoff Escalation Protocol
- [ ] Define `escalate_to_human` tool or intention trigger (e.g. user demands to speak to owner, or sensitive topic detected).
- [ ] Update `Contact.is_muted = True` upon escalation.
- [ ] Send automated acknowledgment to user ("I've notified the owner, they'll respond shortly").
- **Verification**: Trigger escalation keyword; verify subsequent user messages bypass AI while muted.

### Phase 4.2: Owner Control & Unmute Mechanism
- [ ] Implement owner command recognition (e.g., owner sends `/unmute <phone>` or toggles via an internal endpoint).
- [ ] Reset `is_muted = False` and update conversation state.
- **Verification**: Test muting contact, sending messages without AI reply, and resuming AI replies upon unmute.

### Phase 4.3: Resilience, Rate Limiting & Safety Guardrails
- [ ] Add exponential backoff retry for Meta Graph API calls.
- [ ] Add fallback message if Gemini API times out or rate limits.
- [ ] Add structured logging with correlation IDs for each incoming message.
- **Milestone 4 Test**: Simulate API errors and verify clean fallback messages sent to user without server crash.

---

## Phase 5: Production Deployment & Containerization

### Phase 5.1: Production Dockerization
- [ ] Write optimized multi-stage `Dockerfile`.
- [ ] Configure `docker-compose.prod.yml` including FastAPI app, PostgreSQL with pgvector, and persistent volumes.
- [ ] Implement `/health` and `/ready` endpoints for container orchestration.
- **Verification**: Stack boots successfully in Docker with healthchecks passing.

### Phase 5.2: Production Deployment & Webhook Setup Guide
- [ ] Create step-by-step deployment document:
  - Environment provisioning.
  - SSL certificate setup (Nginx / Caddy / Cloudflare).
  - Registering production webhook URL on Meta Developer Portal.
  - Generating permanent System User Access Token on Meta Business Manager.
- **Milestone 5 Test**: Final verification on staging/production environment.
