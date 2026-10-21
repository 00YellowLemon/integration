# Constitution: Mission & Objectives

## 1. Executive Summary
The goal of this project is to build an intelligent, responsive, and privacy-conscious **WhatsApp AI Personal Assistant** using the **Meta Business Cloud API**, **LangChain**, and **Google Gemini 3.8 Flash**.

The system acts as a digital proxy and executive assistant on the owner's WhatsApp number. It autonomously reads inbound messages, retains contextual conversation history, consults custom business documents and tools via an agentic architecture, drafts or dispatches high-quality replies, and seamlessly escalates conversations to the human owner when necessary.

---

## 2. Core Persona & Tone of Voice
- **Role**: Executive & Personal Assistant representing the owner.
- **Tone**: Professional, friendly, concise, and helpful.
- **Voice Guidelines**:
  - Always communicate clearly and politely without being overly robotic or excessively verbose.
  - Mirror the owner's professional standards and boundaries.
  - Transparently state its assistant role when relevant, maintaining credibility and trust.
  - Prioritize actionable answers over conversational filler.

---

## 3. Key Capabilities & Scope

### In-Scope
1. **WhatsApp Messaging I/O**:
   - Reliable webhook ingestion from Meta's WhatsApp Cloud Platform.
   - Sending text messages, status updates (read receipts, typing indicators), and interactive elements.
2. **Context-Aware Conversational Intelligence**:
   - Multi-turn conversation memory keyed by contact phone number.
   - Dynamic message summarization and intention classification (inquiry, scheduling, urgent, casual).
3. **Agentic Tool Calling & Document Retrieval (RAG)**:
   - Tool calling to perform utility actions (checking notes, calendar availability, status checks).
   - Retrieval-Augmented Generation (RAG) over business documents (FAQs, pricing, guides, personal notes).
4. **Human Handoff & Escalation**:
   - Automated detection of sensitive, complex, or explicitly requested human escalations.
   - Mute/pause AI responses when the human owner intervenes or when handoff is triggered.
5. **Robust Data Persistence**:
   - Storing all message logs, conversation states, and contact profiles in PostgreSQL.
   - Idempotency to prevent duplicate message processing from Meta webhook retries.

### Out-of-Scope (Non-Goals)
- Bulk broadcast marketing or unsolicited outbound spam campaigns.
- Autonomous decision-making on financial transactions or sensitive legal agreements without human owner sign-off.
- Unmonitored black-box autonomous actions without audit logging.

---

## 4. Guiding Principles & Operational Standards
1. **Zero Message Loss & High Reliability**: Every inbound webhook event must be acknowledged with HTTP 200 immediately, queued/processed safely, and never dropped.
2. **Idempotency**: Meta webhooks may retry events; the pipeline must deduplicate messages using Meta message IDs (`wamid`).
3. **Speed & Latency**: Leverage Gemini 3.8 Flash's rapid generation capabilities to ensure replies are delivered within 2-5 seconds.
4. **Data Privacy & Security**: Sensitive contact info and messages must be stored securely with encrypted connections and environment-driven credentials.
5. **Modularity**: Separation between the WhatsApp transport layer, the agentic reasoning engine, and the persistence storage.
