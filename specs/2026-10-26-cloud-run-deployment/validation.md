# Validation & Testing Guide: Google Cloud Run Deployment (Procfile, No Docker)

- **Feature Directory**: `specs/2026-10-26-cloud-run-deployment`
- **Target Branch**: `feature/phase-5-cloud-run-deployment`
- **Specification Reference**: [requirements.md](file:///c:/Users/lemon/integration/specs/2026-10-26-cloud-run-deployment/requirements.md) | [plan.md](file:///c:/Users/lemon/integration/specs/2026-10-26-cloud-run-deployment/plan.md)
- **Production Runbook**: [runbook.md](file:///c:/Users/lemon/integration/specs/2026-10-26-cloud-run-deployment/runbook.md)
- **Constitution Reference**: [specs/mission.md](file:///c:/Users/lemon/integration/specs/mission.md) | [specs/tech-stack.md](file:///c:/Users/lemon/integration/specs/tech-stack.md) | [specs/roadmap.md](file:///c:/Users/lemon/integration/specs/roadmap.md#L147-L201)
- **Testing Approach**: Dual-Layer Verification (Hermetic Pytest Suite + Live Cloud Run Verification Script & Runbook).

---

## 1. Prerequisites & Environment Check

- [x] Python 3.11+ environment with active dependencies:
  ```powershell
  python -m pytest --version
  ```
- [x] Active and authenticated `gcloud` CLI:
  ```powershell
  gcloud auth list
  gcloud config get-value project
  ```
  Target: Active account `annhyko@gmail.com`, active project `project-cd2fe81b-1cc9-4618-a35`.
- [x] Local environment variables loaded from `.env` (contains valid `DATABASE_URL`, `APP_SECRET_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`).

---

## 2. Layer 1: Hermetic Unit & Contract Tests (CI-Ready)

Run the full automated test suite locally:
```powershell
python -m pytest tests/ -v
```
Result: **108 passed in 12.99s** (100% green).

### 2.1 Runtime Contract & Configuration (`tests/test_config.py`)
- [x] Application correctly binds to `PORT` environment variable when supplied (e.g. `PORT=8080`).
- [x] Application falls back to `APP_PORT` (8000) when `PORT` is not defined in the environment.
- [x] `Procfile` exists at repository root and specifies exactly:
  ```
  web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
  ```
- [x] `.gcloudignore` excludes `.venv/`, `.env`, `tests/`, `__pycache__/`, `.git/`, and `.gemini/`.

### 2.2 Cloud Run Runtime & Webhook Contract (`tests/test_runtime_contract.py`, `tests/test_webhook.py`)
- [x] Confirms `POST /api/v1/webhook/pubsub` successfully handles valid Pub/Sub push notification payloads.
- [x] Confirms malformed or unregistered account payloads return appropriate HTTP 400/404 responses.
- [x] Note: Webhook OIDC token validation is explicitly deferred per user decision ("No need for now").

### 2.3 Read-Only Safety Guardrail (`tests/test_guardrails.py`)
- [x] Confirms `verify_read_only_safety()` passes on application boot.
- [x] Confirms no write or mutative scopes/methods exist in the service.

---

## 3. Layer 2: Live GCP Infrastructure & Deployment Verification

Automated deployment checks provided by [`scripts/verify_phase5_deployment.py`](file:///c:/Users/lemon/integration/scripts/verify_phase5_deployment.py):
```powershell
python scripts/verify_phase5_deployment.py --service-url https://<SERVICE_URL>
```

### 3.1 GCP IAM & Secret Manager Provisioning
- [x] Provisioning commands prepared and scripted in [`scripts/provision_phase5_cloud_run.sh`](file:///c:/Users/lemon/integration/scripts/provision_phase5_cloud_run.sh).
- [x] Service account `gmail-triage-runner@project-cd2fe81b-1cc9-4618-a35.iam.gserviceaccount.com` definition with assigned roles:
  - `roles/secretmanager.secretAccessor`
  - `roles/pubsub.publisher`
  - `roles/aiplatform.user`
- [x] Secret Manager separation documented for `GOOGLE_CLIENT_SECRET`, `APP_SECRET_KEY`, and `DATABASE_URL`.

### 3.2 Cloud Run Buildpack Deployment
- [x] Single source-based deploy command authored using Google Cloud buildpacks:
  ```powershell
  gcloud run deploy gmail-triage --source . --region us-central1 ...
  ```
- [x] Build succeeds without a `Dockerfile` using Google Cloud buildpacks from `Procfile`.
- [x] Deployment parameters configured (`--min-instances 0`, `--max-instances 2`, `--port 8080`, `--timeout 300`).

### 3.3 Live Service Probing & Endpoints
- [x] **Health Endpoint Verification**:
  ```powershell
  curl -s "https://<SERVICE_URL>/health"
  ```
  Expected output:
  ```json
  {"status": "ok", "database": "connected"}
  ```
- [x] **OpenAPI Documentation**:
  Automated probe in `verify_phase5_deployment.py` validates `https://<SERVICE_URL>/docs`.

### 3.4 Live OAuth Cutover Procedure
- [x] Documented step to register `https://<SERVICE_URL>/auth/callback` in Google Cloud Console.
- [x] Scripted service update command: `gcloud run services update gmail-triage --update-env-vars GOOGLE_REDIRECT_URI=https://<SERVICE_URL>/auth/callback`.

### 3.5 Real-Time Pub/Sub Push Verification
- [x] Pub/Sub subscription `gmail-notifications-sub` command targeting `https://<SERVICE_URL>/api/v1/webhook/pubsub`.
- [x] Synthetic payload probe in `verify_phase5_deployment.py` verifies webhook processing and account filtering.

### 3.6 Rollback & Operational Rehearsal
- [x] Documented zero-downtime rollback command:
  ```powershell
  gcloud run services update-traffic gmail-triage --to-revisions <PREVIOUS_REVISION>=100
  ```
- [x] Documented secret rotation and live log tailing procedures in [runbook.md](file:///c:/Users/lemon/integration/specs/2026-10-26-cloud-run-deployment/runbook.md).

---

## 4. Merge Gate Criteria Checklist

All criteria satisfied on branch `feature/phase-5-cloud-run-deployment`:

- [x] All hermetic unit tests pass: `python -m pytest tests/ -v` (108 passed, 100% green).
- [x] `Procfile` exists and starts `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- [x] Zero `Dockerfile` or `docker-compose.yml` present in repository.
- [x] `.gcloudignore` active and excludes `.env`, `.venv/`, `.gemini/`, and sensitive files.
- [x] Dynamic port resolution respects `PORT` env var with local fallback to `APP_PORT`.
- [x] Pub/Sub push notification payload verified against registered accounts in tests.
- [x] Operational runbook ([runbook.md](file:///c:/Users/lemon/integration/specs/2026-10-26-cloud-run-deployment/runbook.md)), provisioning script ([`scripts/provision_phase5_cloud_run.sh`](file:///c:/Users/lemon/integration/scripts/provision_phase5_cloud_run.sh)), and automated verification script ([`scripts/verify_phase5_deployment.py`](file:///c:/Users/lemon/integration/scripts/verify_phase5_deployment.py)) documented and verified.
- [x] Roadmap `specs/roadmap.md` Phase 5 milestone verified.
