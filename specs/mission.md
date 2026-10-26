# Constitution: Mission & Objectives

## 1. Executive Summary
The goal of this project is to build an intelligent, secure, and privacy-conscious **Gmail AI Email Triage Agent** using the **Google Gmail API**, **Google Cloud Pub/Sub**, **FastAPI**, **PostgreSQL**, **LangChain**, and **Google Gemini 3.8 Flash**.

The system connects to a personal Gmail account (`@gmail.com`) via OAuth 2.0 with strict read-only permissions (`gmail.readonly`). It autonomously ingests inbound emails and conversation threads, cleans and parses complex MIME structures, and deploys an autonomous **LangChain Tool Agent** powered by Google Gemini 3.8 Flash with **PostgreSQL-backed short-term memory checkpointing**.

The Triage Agent categorizes incoming emails, calculates urgency scores, extracts critical action items and deadlines, synthesizes executive thread briefings, and indexes content for semantic search in PostgreSQL. The frontend connects directly to PostgreSQL via the provided connection string (as well as consuming FastAPI REST endpoints) to display real-time inbox triage dashboards.

To ensure absolute safety and trust, the triage assistant operates in a **strictly read-only** capacity—it never sends, drafts, modifies, or deletes emails in the user's mailbox, focusing entirely on inbox intelligence, triage, and automated synthesis.

The service is hosted on **Google Cloud Run**, deployed from source with a repository `Procfile` as its start command. The runtime is stateless and scale-to-zero, with all durable state (accounts, messages, triage results, and agent memory) held in PostgreSQL.

---

## 2. Core Persona & Tone of Voice
- **Role**: Executive AI Email Triage Agent & Inbox Analyst.
- **Tone**: Concise, structured, objective, and privacy-focused.
- **Voice Guidelines**:
  - Deliver immediate, high-signal triage classifications and action items with clear priority indicators (e.g., Urgent, Action Required, Invoices/Receipts, Newsletters, Scheduling, Low Priority).
  - Explicitly identify deadlines, due dates, commitments, and sender expectations.
  - Omit conversational filler; provide clean structured bullets and executive digests.
  - Respect confidentiality and data privacy: operate strictly within authenticated boundaries and store zero unencrypted credentials.

---

## 3. Key Capabilities & Scope

### In-Scope
1. **Gmail OAuth 2.0 Connection & Token Lifecycle**:
   - Secure authorization code flow with offline access (`access_type="offline"`, `prompt="consent"`).
   - Automatic token renewal using refresh tokens securely stored and encrypted in PostgreSQL.
   - Strict read-only scope enforcement (`https://www.googleapis.com/auth/gmail.readonly`).
2. **Inbound Email Ingestion & Parsing**:
   - Synchronization of message threads, headers (From, To, Date, Subject), and body content.
   - Robust MIME decoding handling plain text, multipart, and sanitized HTML.
   - Attachment metadata extraction (file name, type, size) without loading large binary blobs into memory.
   - Real-time mailbox push notifications via Google Cloud Pub/Sub (`users.watch`) backed by fallback delta polling using Gmail `historyId`.
3. **LangChain Tool-Calling Email Triage Agent (Gemini 3.8 Flash)**:
   - **Autonomous Tool Execution**: The agent uses dedicated tools (e.g., email payload inspector, classifier, action item extractor, summary synthesizer) to triage incoming messages.
   - **PostgreSQL Short-Term Memory Checkpointer**: Utilizes LangChain / LangGraph's `PostgresSaver` checkpointer with the provided PostgreSQL connection string to persist thread-level conversational state and context across agent execution turns.
   - **Triage & Classification**: Categorizes emails into defined buckets (Urgent, Action Required, Invoices/Receipts, Scheduling, Updates/Newsletters, General).
   - **Action Item & Deadline Extraction**: Discovers explicit and implicit action items, assignees, and deadlines.
   - **Thread Summarization**: Synthesizes multi-turn email conversations into concise executive briefings.
4. **Relational & Vector Persistence for Backend and Frontend**:
   - Normalized PostgreSQL schema for accounts, threads, messages, triage analyses, and action items.
   - Dedicated LangChain checkpointer tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`) for short-term memory.
   - Frontend access to PostgreSQL via the provided connection string for immediate dashboard consumption.
   - Deduplication and idempotency keyed by unique Gmail `message_id` and `thread_id`.
   - Semantic vector embeddings with `pgvector` for natural language semantic search across past emails and triage briefings.
5. **Digest & Triage APIs**:
   - RESTful endpoints to query inbox digests, urgent triage queues, categorized feeds, and thread analyses.

### Out-of-Scope (Non-Goals)
- **Sending or Modifying Outbound Emails**: The agent will not send, forward, or create draft replies in Gmail.
- **Destructive Mailbox Mutations**: The agent will not delete messages, alter labels, star, or mark spam in the user's Gmail mailbox.
- **Multi-Tenant Public SaaS**: Initial architecture is tailored for single-user / personal account owner connection.

---

## 4. Guiding Principles & Operational Standards
1. **Least Privilege & Read-Only Safety**: The service requests exclusively `gmail.readonly` access. Under no circumstances should write or send permissions (`gmail.send`, `gmail.modify`) be requested or implemented.
2. **Deterministic Triage via Tool Calling**: LLM triage operations leverage LangChain tool calling and structured Pydantic schemas for deterministic data storage and validation.
3. **Resilient Short-Term Memory**: The triage agent stores thread states and checkpoint turns in PostgreSQL via `PostgresSaver`, ensuring interrupted agent runs can resume smoothly without data loss.
4. **Zero Message Loss & Idempotent Ingestion**: Every sync event checks existing records by Gmail `message_id` and updates synchronization state with `historyId` to prevent duplicate or missing records.
5. **Speed & Latency**: Leverage Gemini 3.8 Flash's rapid inference speed to analyze inbound emails within seconds of receipt.
6. **Privacy & Credential Isolation**: OAuth tokens, credentials, and email contents are stored securely with environment-driven secrets and encrypted connections.
7. **Cloud Run-Native Deployment**: Production runs as a single stateless **Google Cloud Run** service started from the `Procfile` (`uvicorn ... --host 0.0.0.0 --port $PORT`). Secrets come from Google Secret Manager, identity from a dedicated service account, and no Dockerfile, Docker Compose, or local container runtime is part of the deployment path.
