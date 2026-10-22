# Constitution: Mission & Objectives

## 1. Executive Summary
The goal of this project is to build an intelligent, secure, and privacy-conscious **Gmail AI Assistant** using the **Google Gmail API**, **Google Cloud Pub/Sub**, **FastAPI**, **PostgreSQL**, and **Google Gemini 3.8 Flash**.

The system connects to a personal Gmail account (`@gmail.com`) via OAuth 2.0 with strict read-only permissions (`gmail.readonly`). It autonomously ingests inbound emails and conversation threads, cleans and parses complex MIME structures, and leverages Google Gemini 3.8 Flash to categorize emails, extract critical action items and deadlines, generate executive digests, and index content for semantic search in PostgreSQL.

To ensure safety and trust, the assistant operates in a **strictly read-only** capacity—it never sends or drafts outgoing emails, focusing entirely on inbox intelligence, triage, and automated synthesis.

---

## 2. Core Persona & Tone of Voice
- **Role**: Executive Inbox Analyst & Triage Assistant.
- **Tone**: Concise, structured, objective, and privacy-focused.
- **Voice Guidelines**:
  - Present summaries and action items in bullet points with priority indicators (e.g., Urgent, Action Required, Informational).
  - Highlight explicit deadlines, dates, and sender expectations cleanly.
  - Omit unnecessary conversational filler and fluff; deliver immediate, high-signal information.
  - Respect confidentiality: never expose or process data outside authenticated boundaries.

---

## 3. Key Capabilities & Scope

### In-Scope
1. **Gmail OAuth 2.0 Connection & Token Lifecycle**:
   - Secure authorization code flow with offline access (`access_type="offline"`, `prompt="consent"`).
   - Automatic token renewal using refresh tokens stored securely in PostgreSQL.
   - Strict read-only scope enforcement (`https://www.googleapis.com/auth/gmail.readonly`).
2. **Inbound Email Ingestion & Parsing**:
   - Synchronization of message threads, headers (From, To, Date, Subject), and body content.
   - Robust MIME decoding handling both plain text and HTML (with sanitization and text extraction).
   - Attachment metadata extraction (file name, type, size) without loading excessive binary payload into memory.
   - Real-time mailbox push notifications via Google Cloud Pub/Sub (`users.watch`) backed by fallback sync polling using Gmail `historyId`.
3. **Context-Aware Conversational Intelligence (Gemini 3.8 Flash)**:
   - **Triage & Classification**: Categorize emails automatically (e.g., Urgent, Action Required, Invoices/Receipts, Newsletters, Scheduling, Low Priority).
   - **Action Item Extraction**: Extract actionable requests, deadlines, meeting invitations, and commitments.
   - **Thread Summarization**: Generate multi-turn conversation digests for long email chains.
4. **Relational & Vector Persistence**:
   - Normalized PostgreSQL schema for accounts, threads, messages, analysis results, and action items.
   - Deduplication and idempotency keyed by unique Gmail `message_id` and `thread_id`.
   - Semantic vector embeddings with `pgvector` to allow natural language search across past emails.
5. **Digest & Triage APIs**:
   - RESTful endpoints to query inbox digests, urgent items, categorized feeds, and thread analyses.

### Out-of-Scope (Non-Goals)
- **Sending or Drafting Outbound Emails**: The system will not send, forward, or draft outgoing emails.
- **Destructive Actions**: The system will not delete, mark spam, or alter labels in the user's Gmail mailbox unless explicitly added in a future phase with dedicated permissions.
- **Multi-Tenant Public SaaS**: Initial architecture is tailored for single-user / personal account owner connection.

---

## 4. Guiding Principles & Operational Standards
1. **Least Privilege & Read-Only Safety**: The service requests exclusively `gmail.readonly` access. Under no circumstances should write or send permissions (`gmail.send`, `gmail.modify`) be requested.
2. **Zero Message Loss & Idempotent Ingestion**: Every sync event checks existing records by Gmail `message_id` and updates synchronization state with `historyId` to prevent duplicate or missing records.
3. **Structured Intelligence**: LLM outputs from Gemini 3.8 Flash must strictly adhere to typed Pydantic schemas (JSON mode / tool calling) for deterministic parsing and storage.
4. **Speed & Latency**: Leverage Gemini 3.8 Flash's rapid inference speed to analyze inbound emails within seconds of receipt.
5. **Privacy & Credential Isolation**: OAuth tokens, credentials, and email contents are stored securely with environment-driven secrets and encrypted connections.
