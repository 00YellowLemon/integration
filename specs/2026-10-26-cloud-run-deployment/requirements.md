# Feature Requirements: Google Cloud Run Deployment (Procfile, No Docker)

- **Feature Directory**: `specs/2026-10-26-cloud-run-deployment`
- **Roadmap Reference**: [Phase 5 (5.1 - 5.7)](file:///c:/Users/lemon/integration/specs/roadmap.md#L147-L201)
- **Status**: Approved with User Scope Confirmation (Region: us-central1, Default Scaling, Webhook OIDC deferred per user directive)
- **Target Branch**: `feature/phase-5-cloud-run-deployment`
- **Branch Base**: `feature/phase-4-frontend-views-digest-search`
- **Constitution Guidance**: [specs/mission.md](file:///c:/Users/lemon/integration/specs/mission.md) | [specs/tech-stack.md](file:///c:/Users/lemon/integration/specs/tech-stack.md)

---

## 1. Executive Summary & Context

Phases 1 through 4 completed the full local and testable intelligence core of the **Gmail AI Email Triage Agent**:
1. **Phase 1**: Google OAuth 2.0 flow with strict `gmail.readonly` permissions, AES-GCM encrypted token persistence, and Gmail API client verification.
2. **Phase 2**: Relational schema in PostgreSQL (`accounts`, `threads`, `messages`), sanitized MIME decoding, idempotent ingestion, and delta sync worker.
3. **Phase 3**: Autonomous LangChain Email Triage Agent powered by Google Gemini 3.8 Flash on Vertex AI (ADC), with `AsyncPostgresSaver` short-term memory checkpointer, structured tool-calling (`classify_urgency_and_category`, `extract_action_items_and_deadlines`, `synthesize_thread_summary`), and background triage execution.
4. **Phase 4**: Frontend PostgreSQL views (`v_urgent_triage_feed`, `v_pending_action_items`, `v_today_digest`), strategic composite indexes, FastAPI REST digest APIs (`/api/v1/digest/today`, `/api/v1/action-items`, `/api/v1/threads/{thread_id}/triage`), async exponential backoff with `tenacity`, and startup read-only guardrails.

**Phase 5 delivers production hosting on Google Cloud Run**:
As dictated by [specs/mission.md §1 & §4](file:///c:/Users/lemon/integration/specs/mission.md) and [specs/tech-stack.md §1 & §6](file:///c:/Users/lemon/integration/specs/tech-stack.md), production deployment is **Cloud Run-native, source-deployed via Google Cloud Buildpacks, and driven exclusively by a repository `Procfile`**.
- **Strictly No Docker**: There is no `Dockerfile` and no Docker Compose.
- **Stateless Runtime**: Ephemeral container filesystem; durable state lives exclusively in PostgreSQL.
- **Keyless Security Model**: Application Default Credentials (ADC) bound to a least-privilege service account (`gmail-triage-runner`), with sensitive secrets (`GOOGLE_CLIENT_SECRET`, `APP_SECRET_KEY`, `DATABASE_URL`) injected via Google Secret Manager.
- **Pub/Sub Push Integration**: Mailbox watch events push directly to the Cloud Run HTTPS URL (`/api/v1/webhook/pubsub`), triggering background delta sync and triage. OIDC JWT verification is deferred per user decision, relying on payload structure and verified account matching.

---

## 2. Scope

### In-Scope (Roadmap 5.1 - 5.7)

1. **Procfile & Cloud Run Runtime Contract (Roadmap 5.1)**:
   - Add `Procfile` at repository root defining the `web` process:
     ```
     web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
     ```
   - Update application configuration to respect the runtime `$PORT` environment variable injected by Cloud Run (defaulting to `8000` locally or `8080` in production).
   - Add `.gcloudignore` excluding development artifacts, virtual environments (`.venv/`), test caches, git metadata, and local `.env` files.
   - Verify stateless contract: confirm zero local disk writes and verify local execution with `PORT=8080`.

2. **GCP Project, APIs & Service Account (Roadmap 5.2)**:
   - Bind to active GCP project `project-cd2fe81b-1cc9-4618-a35`.
   - Enable required Google Cloud APIs: `run.googleapis.com`, `cloudbuild.googleapis.com`, `artifactregistry.googleapis.com`, `secretmanager.googleapis.com`, `pubsub.googleapis.com`, `aiplatform.googleapis.com`.
   - Create least-privilege dedicated service account: `gmail-triage-runner@project-cd2fe81b-1cc9-4618-a35.iam.gserviceaccount.com`.
   - Grant necessary IAM roles:
     - `roles/secretmanager.secretAccessor` (access runtime secrets)
     - `roles/pubsub.publisher` (register Pub/Sub watches)
     - `roles/aiplatform.user` (invoke Vertex AI Gemini 3.8 Flash model)

3. **Secrets Management via Google Secret Manager (Roadmap 5.3)**:
   - Provision secrets in Secret Manager:
     - `GOOGLE_CLIENT_SECRET`
     - `APP_SECRET_KEY`
     - `DATABASE_URL`
   - Add current secret values securely via stdin (`--data-file=-`).
   - Map non-sensitive settings as plain environment variables:
     - `GOOGLE_CLIENT_ID`, `GOOGLE_REDIRECT_URI`, `GCP_PROJECT_ID`, `PUBSUB_TOPIC_NAME`, `PUBSUB_SUBSCRIPTION_NAME`, `LOG_LEVEL`, `GOOGLE_CLOUD_LOCATION`, `TRIAGE_ENABLED`.

4. **Source Deployment to Cloud Run (Roadmap 5.4)**:
   - Deploy from source using `gcloud run deploy gmail-triage --source .`:
     - Region: `us-central1`.
     - Service Account: `gmail-triage-runner@project-cd2fe81b-1cc9-4618-a35.iam.gserviceaccount.com`.
     - Flags: `--allow-unauthenticated`, `--port 8080`, `--min-instances 0`, `--max-instances 2`, `--timeout 300`.
     - Secret mounts: `--set-secrets`.
     - Environment mounts: `--set-env-vars`.
   - Confirm buildpacks assemble Python container, start Uvicorn from `Procfile`, and run startup database and checkpointer init.
   - Verify `/health` responds with HTTP 200 and `"database":"connected"`.

5. **OAuth Redirect URI Cutover (Roadmap 5.5)**:
   - Add `https://<SERVICE_URL>/auth/callback` to Authorized Redirect URIs in Google Cloud Console.
   - Update Cloud Run environment variable `GOOGLE_REDIRECT_URI=https://<SERVICE_URL>/auth/callback`.
   - Verify live authentication flow through the Cloud Run HTTPS service URL.

6. **Pub/Sub Push Wiring to Cloud Run (Roadmap 5.6)**:
   - Wire Pub/Sub push subscription targeting `https://<SERVICE_URL>/api/v1/webhook/pubsub`.
   - Retain lightweight payload validation and registered account matching (OIDC token verification deferred per user decision).
   - Re-register mailbox watch with Gmail API.

7. **Production Verification, Observability & Rollback Runbook (Roadmap 5.7)**:
   - Tail live logs with `gcloud run services logs tail gmail-triage`.
   - End-to-end live verification script: `scripts/verify_phase5_deployment.py`.
   - Rollback rehearsal and operational runbook documenting revision cutover and emergency rollback commands.

### Out-of-Scope (Non-Goals)

- **OIDC JWT Verification on Webhook**: Explicitly deferred per user confirmation ("No need for now").
- **Docker / Containerfile**: Strictly prohibited per constitution. Google Cloud Buildpacks handles build steps.
- **Docker Compose or Kubernetes**: Not used; Google Cloud Run serverless runtime only.
- **Outbound Email Mutations**: Sending/drafting/deleting remain prohibited by constitutional guardrails.
- **pgvector Semantic Search**: Retained as deferred per user directive in Phase 4.

---

## 3. Locked Architectural Decisions

| ID | Decision | Rationale |
|---|---|---|
| **D1** | **Procfile-based Source Deployment (`--source .`) without Dockerfile**. | Mandated by [specs/tech-stack.md §6.1](file:///c:/Users/lemon/integration/specs/tech-stack.md) and [specs/roadmap.md §5](file:///c:/Users/lemon/integration/specs/roadmap.md). Standardizes Python runtime using Cloud Native Buildpacks without maintaining Dockerfiles. |
| **D2** | **Region set to `us-central1`**. | Low-latency co-location for Google Cloud Run, Secret Manager, and Vertex AI Gemini 3.8 Flash global endpoint. |
| **D3** | **Default Serverless Scaling (`--min-instances 0`, `--max-instances 2`)**. | Scales to zero when idle for minimal cost, burst-capable for real-time webhooks. |
| **D4** | **Application Default Credentials (ADC) via Dedicated Service Account**. | Avoids downloading or storing service account JSON keys. Identity `gmail-triage-runner` carries only Secret Manager read, Pub/Sub publish, and Vertex AI invoke roles. |
| **D5** | **Secret Separation**: Secret Manager for credentials, env vars for non-secrets. | High-security posture: database connection strings, client secrets, and app keys are versioned in Secret Manager and injected at runtime by Cloud Run. |
| **D6** | **Defer Webhook OIDC JWT verification**. | User confirmed "No need for now"; incoming Pub/Sub push payloads are authenticated by matching the registered email and historyId in PostgreSQL. |
| **D7** | **Dual-Environment Port Binding**. | App dynamically binds to `PORT` environment variable (injected by Cloud Run, e.g. 8080) with fallback to `APP_PORT` (8000) for local development. |

---

## 4. Cloud Run Architecture & Component Topology

```
                                  ┌───────────────────────────┐
                                  │   Google Cloud Console    │
                                  │ (OAuth Redirect Cutover)  │
                                  └─────────────┬─────────────┘
                                                │
               User Browser                     │ Webhook Push
        (GET /auth/login, /docs)                │ (POST /api/v1/webhook/pubsub)
                     │                          │ [with OIDC Bearer Token]
                     ▼                          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│              Google Cloud Run Service (`gmail-triage`)                   │
│                                                                          │
│  - Built via Cloud Buildpacks from repository Procfile                   │
│  - Process: uvicorn app.main:app --host 0.0.0.0 --port $PORT             │
│  - Identity: gmail-triage-runner@<PROJECT_ID>.iam.gserviceaccount.com    │
│  - Concurrency: 80 | Min instances: 0 | Max instances: 2                 │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ FastAPI App Layer                                                  │  │
│  │ - OIDC Validator (verifies Google token signature & audience)      │  │
│  │ - Read-Only Guardrail (enforces gmail.readonly at boot)            │  │
│  │ - OAuth Router (/auth/login, /auth/callback)                       │  │
│  │ - Triage & Ingestion Worker (pubsub webhook, delta sync)           │  │
│  │ - LangChain Triage Agent (Gemini 3.8 Flash via ADC)                │  │
│  │ - Digest & Action Items APIs (/api/v1/digest/today, etc.)          │  │
│  └─────────────────────────────────┬──────────────────────────────────┘  │
└────────────────────────────────────┼─────────────────────────────────────┘
                                     │
           ┌─────────────────────────┴────────────────────────┐
           ▼                                                  ▼
┌───────────────────────────┐                      ┌───────────────────────┐
│   Google Secret Manager   │                      │  Managed PostgreSQL   │
│  - GOOGLE_CLIENT_SECRET   │                      │  - Accounts & Tokens  │
│  - APP_SECRET_KEY         │                      │  - Threads & Messages │
│  - DATABASE_URL           │                      │  - Triage & Analyses  │
└───────────────────────────┘                      │  - LangGraph Memory   │
                                                   └───────────────────────┘
```

---

## 5. Security & Identity Specifications

1. **Service Account Configuration**:
   - Name: `gmail-triage-runner`
   - Email: `gmail-triage-runner@project-cd2fe81b-1cc9-4618-a35.iam.gserviceaccount.com`
   - Assigned Roles:
     - `roles/secretmanager.secretAccessor`
     - `roles/pubsub.publisher`
     - `roles/aiplatform.user`
2. **Secret Manager Secrets**:
   - `projects/project-cd2fe81b-1cc9-4618-a35/secrets/GOOGLE_CLIENT_SECRET`
   - `projects/project-cd2fe81b-1cc9-4618-a35/secrets/APP_SECRET_KEY`
   - `projects/project-cd2fe81b-1cc9-4618-a35/secrets/DATABASE_URL`
3. **OIDC Authentication Pipeline**:
   - Explicitly deferred per user decision ("No need for now").
   - Incoming Pub/Sub push payloads are authenticated by payload validation and matching registered accounts in PostgreSQL (D6).
