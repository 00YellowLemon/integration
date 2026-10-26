# Implementation Plan: Google Cloud Run Deployment (Procfile, No Docker)

- **Feature Directory**: `specs/2026-10-26-cloud-run-deployment`
- **Target Branch**: `feature/phase-5-cloud-run-deployment`
- **Specification Reference**: [requirements.md](file:///c:/Users/lemon/integration/specs/2026-10-26-cloud-run-deployment/requirements.md)
- **Constitution Guidance**: [specs/mission.md](file:///c:/Users/lemon/integration/specs/mission.md) | [specs/tech-stack.md](file:///c:/Users/lemon/integration/specs/tech-stack.md) | [specs/roadmap.md](file:///c:/Users/lemon/integration/specs/roadmap.md#L147-L201)
- **Validation**: [validation.md](file:///c:/Users/lemon/integration/specs/2026-10-26-cloud-run-deployment/validation.md)

---

## Task Group 1: Procfile, Runtime Port Binding & `.gcloudignore`

- [ ] **Task 1.1: Create Repository `Procfile`**
  - Add `Procfile` at repository root defining the web process:
    ```
    web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
    ```
  - Verify line endings and format for compatibility with Google Cloud buildpacks.

- [ ] **Task 1.2: Dynamic Port Binding & Host Configuration in Application**
  - In `app/core/config.py`: Update `APP_PORT` default / environment binding to inspect `PORT` env var (standard Cloud Run injection) if present, falling back to `APP_PORT` / 8000.
  - In `app/core/config.py`: Ensure `APP_HOST` can be set to `0.0.0.0` or defaulted to `0.0.0.0` when running in containerized / cloud mode.
  - Verify application boots cleanly locally with `PORT=8080` without container runtime.

- [ ] **Task 1.3: Author Comprehensive `.gcloudignore`**
  - Create `.gcloudignore` in repository root excluding:
    - Python virtual environments (`.venv/`, `venv/`, `env/`)
    - Bytecode & caches (`__pycache__/`, `*.pyc`, `.pytest_cache/`)
    - Sensitive files (`.env`, `credentials.json`, `token.json`, `client_secrets*.json`)
    - Test directories and scripts (`tests/`, `scripts/`, `.git/`, `.gemini/`)
  - Run dry-run upload check to ensure small source upload footprint.

---

## Task Group 2: GCP Project, APIs & Service Account IAM Provisioning

- [ ] **Task 2.1: Verify Active GCP Project Context**
  - Verify active configuration: `gcloud config set project project-cd2fe81b-1cc9-4618-a35`.
  - Confirm active authenticated identity: `gcloud auth list`.

- [ ] **Task 2.2: Enable Required GCP APIs**
  - Execute API enablement for:
    - `run.googleapis.com` (Cloud Run Admin API)
    - `cloudbuild.googleapis.com` (Cloud Build API for buildpacks)
    - `artifactregistry.googleapis.com` (Container image storage)
    - `secretmanager.googleapis.com` (Secret Manager API)
    - `pubsub.googleapis.com` (Cloud Pub/Sub API)
    - `aiplatform.googleapis.com` (Vertex AI API for Gemini 3.8 Flash)

- [ ] **Task 2.3: Provision Dedicated Cloud Run Service Account**
  - Create service account:
    ```bash
    gcloud iam service-accounts create gmail-triage-runner \
      --description="Dedicated runtime identity for Gmail AI Triage Cloud Run service" \
      --display-name="Gmail Triage Cloud Run Runner"
    ```
  - Assign least-privilege IAM roles:
    - `roles/secretmanager.secretAccessor`
    - `roles/pubsub.publisher`
    - `roles/aiplatform.user`
  - Verify service account creation and IAM bindings with `gcloud projects get-iam-policy`.

---

## Task Group 3: Google Secret Manager Provisioning & Environment Contract

- [ ] **Task 3.1: Create Secrets in Google Secret Manager**
  - Provision secrets for sensitive configuration items:
    - `GOOGLE_CLIENT_SECRET`
    - `APP_SECRET_KEY`
    - `DATABASE_URL`
  - Populate initial versions using current values from `.env` via stdin (`--data-file=-`).

- [ ] **Task 3.2: Verify Secret Access for Service Account**
  - Test secret access using service account impersonation or verification command:
    ```bash
    gcloud secrets versions access latest --secret=DATABASE_URL
    ```
  - Ensure secrets never appear in source code or repository git history.

- [ ] **Task 3.3: Document Secret Rotation and Environment Schema**
  - Record environment variable division:
    - Secrets: `GOOGLE_CLIENT_SECRET`, `APP_SECRET_KEY`, `DATABASE_URL`
    - Plain env vars: `GOOGLE_CLIENT_ID`, `GOOGLE_REDIRECT_URI`, `GCP_PROJECT_ID`, `PUBSUB_TOPIC_NAME`, `PUBSUB_SUBSCRIPTION_NAME`, `LOG_LEVEL`, `GOOGLE_CLOUD_LOCATION`, `TRIAGE_ENABLED`

---

## Task Group 4: Webhook Payload & Production Runtime Contract

- [ ] **Task 4.1: Verify Webhook Payload Contract (`app/api/webhook.py`)**
  - Verify `POST /api/v1/webhook/pubsub` properly handles Google Cloud Pub/Sub push notification format.
  - Ensure decoded payload validates `emailAddress` and `historyId`, matching registered accounts in PostgreSQL.
  - Note: OIDC JWT verification is explicitly deferred per user decision ("No need for now").

- [ ] **Task 4.2: Health, Root & CORS Verification for Cloud Run**
  - Verify `/health` reports `{"status":"ok","database":"connected"}`.
  - Verify `/docs` and `/` endpoints work seamlessly over Cloud Run HTTPS without mixed-content issues.
  - Ensure CORS and trusted host headers work properly behind Google Front End (GFE).

- [ ] **Task 4.3: Author Hermetic Unit Tests for Cloud Run Runtime Contract**
  - `tests/test_runtime_contract.py`:
    - Port binding precedence (`PORT` vs `APP_PORT`).
    - Cloud Run health probe responses.
    - Webhook payload compatibility under production settings.

---

## Task Group 5: First Source Deployment to Cloud Run & OAuth Cutover

- [ ] **Task 5.1: Execute First Source Deploy via Google Cloud Buildpacks**
  - Deploy without Dockerfile:
    ```bash
    gcloud run deploy gmail-triage \
      --source . \
      --region us-central1 \
      --service-account gmail-triage-runner@project-cd2fe81b-1cc9-4618-a35.iam.gserviceaccount.com \
      --allow-unauthenticated \
      --port 8080 \
      --min-instances 0 \
      --max-instances 2 \
      --timeout 300 \
      --set-secrets GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:latest,APP_SECRET_KEY=APP_SECRET_KEY:latest,DATABASE_URL=DATABASE_URL:latest \
      --set-env-vars GOOGLE_CLIENT_ID=<CLIENT_ID>,GCP_PROJECT_ID=project-cd2fe81b-1cc9-4618-a35,PUBSUB_TOPIC_NAME=gmail-notifications,PUBSUB_SUBSCRIPTION_NAME=gmail-notifications-sub,GOOGLE_CLOUD_LOCATION=global,TRIAGE_ENABLED=true
    ```
  - Capture deployed HTTPS Service URL (`SERVICE_URL`).

- [ ] **Task 5.2: Verify Cloud Run Health & DB Initialization**
  - Query `curl -s https://<SERVICE_URL>/health`.
  - Confirm HTTP 200 response with `{"status":"ok","database":"connected"}`.
  - Tail deployment logs: `gcloud run services logs tail gmail-triage`.

- [ ] **Task 5.3: OAuth 2.0 Redirect URI Cutover**
  - Add `https://<SERVICE_URL>/auth/callback` to Google Cloud Console Authorized Redirect URIs.
  - Update service env var:
    ```bash
    gcloud run services update gmail-triage \
      --region us-central1 \
      --update-env-vars GOOGLE_REDIRECT_URI=https://<SERVICE_URL>/auth/callback
    ```
  - Test browser login flow: navigate to `https://<SERVICE_URL>/auth/login`, complete consent, verify tokens updated in PostgreSQL.

---

## Task Group 6: Pub/Sub Push Wiring, Verification Script & Rollback Runbook

- [ ] **Task 6.1: Pub/Sub Topic and Push Subscription Configuration**
  - Verify Pub/Sub topic `gmail-notifications` exists.
  - Create/update push subscription targeting `https://<SERVICE_URL>/api/v1/webhook/pubsub`:
    ```bash
    gcloud pubsub subscriptions create gmail-notifications-sub \
      --topic=gmail-notifications \
      --push-endpoint=https://<SERVICE_URL>/api/v1/webhook/pubsub \
      --push-auth-service-account=gmail-triage-runner@project-cd2fe81b-1cc9-4618-a35.iam.gserviceaccount.com \
      --ack-deadline=60
    ```
  - Re-register Gmail mailbox watch via `POST https://<SERVICE_URL>/api/v1/gmail/watch`.

- [ ] **Task 6.2: Build Live Deployment Verification Script (`scripts/verify_phase5_deployment.py`)**
  - Automate checks against live Cloud Run URL:
    1. Probe `/health` (verifies DB connectivity).
    2. Probe `/api/v1/digest/today` (verifies digest views).
    3. Test unauthorized push to `/api/v1/webhook/pubsub` receives HTTP 401.
    4. Send test Pub/Sub notification and verify execution.

- [ ] **Task 6.3: Author Production Operations & Rollback Runbook**
  - Document zero-downtime rollback command:
    ```bash
    gcloud run services update-traffic gmail-triage --to-revisions <PREVIOUS_REVISION>=100
    ```
  - Document secret rotation procedures and log monitoring workflows.
