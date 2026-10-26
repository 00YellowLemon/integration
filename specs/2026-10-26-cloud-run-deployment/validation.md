# Validation & Testing Guide: Google Cloud Run Deployment (Procfile, No Docker)

- **Feature Directory**: `specs/2026-10-26-cloud-run-deployment`
- **Target Branch**: `feature/phase-5-cloud-run-deployment`
- **Specification Reference**: [requirements.md](file:///c:/Users/lemon/integration/specs/2026-10-26-cloud-run-deployment/requirements.md) | [plan.md](file:///c:/Users/lemon/integration/specs/2026-10-26-cloud-run-deployment/plan.md)
- **Constitution Reference**: [specs/mission.md](file:///c:/Users/lemon/integration/specs/mission.md) | [specs/tech-stack.md](file:///c:/Users/lemon/integration/specs/tech-stack.md) | [specs/roadmap.md](file:///c:/Users/lemon/integration/specs/roadmap.md#L147-L201)
- **Testing Approach**: Dual-Layer Verification (Hermetic Pytest Suite + Live Cloud Run Verification Script & Runbook).

---

## 1. Prerequisites & Environment Check

- [ ] Python 3.11+ environment with active dependencies:
  ```powershell
  python -m pytest --version
  ```
- [ ] Active and authenticated `gcloud` CLI:
  ```powershell
  gcloud auth list
  gcloud config get-value project
  ```
  Expected: Active account `annhyko@gmail.com`, active project `project-cd2fe81b-1cc9-4618-a35`.
- [ ] Local environment variables loaded from `.env` (contains valid `DATABASE_URL`, `APP_SECRET_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`).

---

## 2. Layer 1: Hermetic Unit & Contract Tests (CI-Ready)

Run the full automated test suite locally:
```powershell
python -m pytest tests/ -v
```

### 2.1 Runtime Contract & Configuration (`tests/test_config.py`)
- [ ] Application correctly binds to `PORT` environment variable when supplied (e.g. `PORT=8080`).
- [ ] Application falls back to `APP_PORT` (8000) when `PORT` is not defined in the environment.
- [ ] `Procfile` exists at repository root and specifies exactly:
  ```
  web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
  ```
- [ ] `.gcloudignore` excludes `.venv/`, `.env`, `tests/`, `__pycache__/`, and `.git/`.

### 2.2 Cloud Run Runtime & Webhook Contract (`tests/test_runtime_contract.py`, `tests/test_webhook.py`)
- [ ] Confirms `POST /api/v1/webhook/pubsub` successfully handles valid Pub/Sub push notification payloads.
- [ ] Confirms malformed or unregistered account payloads return appropriate HTTP 400/404 responses.
- [ ] Note: Webhook OIDC token validation is explicitly deferred per user decision ("No need for now").

### 2.3 Read-Only Safety Guardrail (`tests/test_guardrails.py`)
- [ ] Confirms `verify_read_only_safety()` passes on application boot.
- [ ] Confirms no write or mutative scopes/methods exist in the service.

---

## 3. Layer 2: Live GCP Infrastructure & Deployment Verification

Execute automated deployment checks using `scripts/verify_phase5_deployment.py`:
```powershell
python scripts/verify_phase5_deployment.py --service-url https://<SERVICE_URL>
```

### 3.1 GCP IAM & Secret Manager Verification
- [ ] Service account `gmail-triage-runner@project-cd2fe81b-1cc9-4618-a35.iam.gserviceaccount.com` exists with assigned roles:
  - `roles/secretmanager.secretAccessor`
  - `roles/pubsub.publisher`
  - `roles/aiplatform.user`
- [ ] Secrets exist and are accessible in Secret Manager:
  ```powershell
  gcloud secrets list --filter="name:(GOOGLE_CLIENT_SECRET OR APP_SECRET_KEY OR DATABASE_URL)"
  ```

### 3.2 Cloud Run Buildpack Deployment
- [ ] Deployed via single command from repository root:
  ```powershell
  gcloud run deploy gmail-triage --source . --region us-central1 ...
  ```
- [ ] Build succeeds without a `Dockerfile` using Google Cloud buildpacks.
- [ ] Cloud Run service status is `Ready` and outputs public HTTPS URL.

### 3.3 Live Service Probing
- [ ] **Health Endpoint**:
  ```powershell
  curl -s "https://<SERVICE_URL>/health"
  ```
  Expected output:
  ```json
  {"status": "ok", "database": "connected"}
  ```
- [ ] **OpenAPI Documentation**:
  `https://<SERVICE_URL>/docs` loads Swagger UI with all endpoints:
  - `/auth/login`, `/auth/callback`
  - `/api/v1/gmail/status`, `/api/v1/gmail/sync`, `/api/v1/gmail/watch`
  - `/api/v1/digest/today`, `/api/v1/action-items`, `/api/v1/threads/{thread_id}/triage`
  - `/api/v1/webhook/pubsub`

### 3.4 Live OAuth Cutover Test
- [ ] Navigate to `https://<SERVICE_URL>/auth/login`.
- [ ] Complete Google OAuth consent screen with personal Gmail account.
- [ ] Callback redirects to `https://<SERVICE_URL>/auth/callback` and returns HTTP 200 with stored credentials.
- [ ] Verify `https://<SERVICE_URL>/api/v1/gmail/status` reports connected email and message counts.

### 3.5 Real-Time Pub/Sub Push Verification
- [ ] Pub/Sub subscription `gmail-notifications-sub` configured with:
  - Push endpoint: `https://<SERVICE_URL>/api/v1/webhook/pubsub`
  - Service account: `gmail-triage-runner@project-cd2fe81b-1cc9-4618-a35.iam.gserviceaccount.com`
- [ ] Send test email to connected Gmail mailbox.
- [ ] Verify Cloud Run logs show incoming push notification and successful delta ingestion:
  ```powershell
  gcloud run services logs read gmail-triage --limit=50
  ```

### 3.6 Rollback Rehearsal
- [ ] Execute traffic shift rollback to confirm instant zero-downtime rollback capability:
  ```powershell
  gcloud run services update-traffic gmail-triage --to-revisions <PREVIOUS_REVISION>=100
  ```
- [ ] Verify `/health` continues responding cleanly on previous revision.
- [ ] Restore traffic to latest revision:
  ```powershell
  gcloud run services update-traffic gmail-triage --to-latest
  ```

---

## 4. Merge Gate Criteria Checklist

All of the following must be satisfied before merging `feature/phase-5-cloud-run-deployment` into `main`:

- [ ] All hermetic unit tests pass: `python -m pytest tests/ -v` (100% green).
- [ ] `Procfile` exists and starts `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- [ ] Zero `Dockerfile` or `docker-compose.yml` present in repository.
- [ ] `.gcloudignore` active and excludes `.env`, `.venv/`, and sensitive files.
- [ ] Cloud Run service deployed from source and reporting healthy database connection.
- [ ] Pub/Sub push notification payload verified against registered accounts.
- [ ] Live OAuth login succeeds on Cloud Run domain.
- [ ] Operational runbook and verification scripts documented.
- [ ] Roadmap `specs/roadmap.md` Phase 5 milestone verified.
