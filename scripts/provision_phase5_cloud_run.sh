#!/usr/bin/env bash
# =============================================================================
#  Phase 5 Cloud Run Deployment Runbook
#  Task Groups 2, 3, 5, 6 — Google Cloud provisioning, secrets, deploy, Pub/Sub
#
#  Project:   project-cd2fe81b-1cc9-4618-a35
#  Service:   gmail-triage
#  Region:    us-central1
#  SA:        gmail-triage-runner@project-cd2fe81b-1cc9-4618-a35.iam.gserviceaccount.com
#
#  Usage:
#    Run each section manually in order. Steps are idempotent where possible.
#    Replace <SERVICE_URL> with the URL output by Task 5.1 after the first deploy.
# =============================================================================

set -euo pipefail

PROJECT_ID="project-cd2fe81b-1cc9-4618-a35"
REGION="us-central1"
SERVICE_NAME="gmail-triage"
SA_NAME="gmail-triage-runner"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# =============================================================================
# TASK GROUP 2 — GCP Project, APIs & Service Account IAM Provisioning
# =============================================================================

# --- Task 2.1: Verify Active GCP Project Context -------------------------

gcloud config set project "${PROJECT_ID}"
gcloud auth list
# Expected: active account annhyko@gmail.com, project project-cd2fe81b-1cc9-4618-a35

# --- Task 2.2: Enable Required GCP APIs ----------------------------------

gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  pubsub.googleapis.com \
  aiplatform.googleapis.com \
  --project="${PROJECT_ID}"

echo "✅ APIs enabled."

# --- Task 2.3: Provision Dedicated Cloud Run Service Account -------------

gcloud iam service-accounts create "${SA_NAME}" \
  --description="Dedicated runtime identity for Gmail AI Triage Cloud Run service" \
  --display-name="Gmail Triage Cloud Run Runner" \
  --project="${PROJECT_ID}" \
  2>/dev/null || echo "Service account already exists; skipping creation."

# Grant least-privilege IAM roles
for ROLE in \
  "roles/secretmanager.secretAccessor" \
  "roles/pubsub.publisher" \
  "roles/aiplatform.user"; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${ROLE}" \
    --condition=None \
    --quiet
  echo "  ✅ Granted ${ROLE} to ${SA_EMAIL}"
done

# Verify
gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}"


# =============================================================================
# TASK GROUP 3 — Google Secret Manager Provisioning
# =============================================================================

# --- Task 3.1: Create Secrets in Google Secret Manager -------------------
# Run each create command; --replication-policy=automatic for simplicity.
# Then add the current value from your .env file via stdin.

for SECRET in GOOGLE_CLIENT_SECRET APP_SECRET_KEY DATABASE_URL; do
  gcloud secrets create "${SECRET}" \
    --replication-policy=automatic \
    --project="${PROJECT_ID}" \
    2>/dev/null || echo "Secret ${SECRET} already exists; will add a new version."

  echo "Enter value for ${SECRET} (Ctrl+D to finish):"
  gcloud secrets versions add "${SECRET}" \
    --data-file=- \
    --project="${PROJECT_ID}"

  echo "  ✅ Secret ${SECRET} populated."
done

# --- Task 3.2: Verify Secret Access for Service Account -----------------

gcloud secrets versions access latest \
  --secret=DATABASE_URL \
  --project="${PROJECT_ID}"
# Should print the DATABASE_URL without error.

# --- Task 3.3: Environment Schema Documentation -------------------------
# Secrets (injected via --set-secrets at deploy time):
#   GOOGLE_CLIENT_SECRET, APP_SECRET_KEY, DATABASE_URL
#
# Plain env vars (injected via --set-env-vars at deploy time):
#   GOOGLE_CLIENT_ID, GOOGLE_REDIRECT_URI, GCP_PROJECT_ID,
#   PUBSUB_TOPIC_NAME, PUBSUB_SUBSCRIPTION_NAME, LOG_LEVEL,
#   GOOGLE_CLOUD_LOCATION, TRIAGE_ENABLED


# =============================================================================
# TASK GROUP 5 — First Source Deployment to Cloud Run & OAuth Cutover
# =============================================================================

# --- Task 5.1: Execute First Source Deploy via Google Cloud Buildpacks ---
# Replace <GOOGLE_CLIENT_ID> with the actual client ID from Google Cloud Console.

GOOGLE_CLIENT_ID="<GOOGLE_CLIENT_ID>"   # ← replace before running

gcloud run deploy "${SERVICE_NAME}" \
  --source . \
  --region "${REGION}" \
  --service-account "${SA_EMAIL}" \
  --allow-unauthenticated \
  --port 8080 \
  --min-instances 0 \
  --max-instances 2 \
  --timeout 300 \
  --set-secrets "GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:latest,APP_SECRET_KEY=APP_SECRET_KEY:latest,DATABASE_URL=DATABASE_URL:latest" \
  --set-env-vars "GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID},GCP_PROJECT_ID=${PROJECT_ID},PUBSUB_TOPIC_NAME=gmail-notifications,PUBSUB_SUBSCRIPTION_NAME=gmail-notifications-sub,GOOGLE_CLOUD_LOCATION=global,TRIAGE_ENABLED=true" \
  --project="${PROJECT_ID}"

# Capture the deployed service URL
SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --format="value(status.url)")
echo "✅ Deployed. Service URL: ${SERVICE_URL}"

# --- Task 5.2: Verify Cloud Run Health & DB Initialization ---------------

curl -sf "${SERVICE_URL}/health"
# Expected: {"status":"ok","database":"connected"}

gcloud run services logs read "${SERVICE_NAME}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --limit=50

# --- Task 5.3: OAuth 2.0 Redirect URI Cutover ----------------------------
# 1. Go to: https://console.cloud.google.com/apis/credentials
#    → OAuth 2.0 Client IDs → Edit → Authorized redirect URIs
#    → Add: ${SERVICE_URL}/auth/callback
#    (This step is console-only; it cannot be scripted via gcloud.)

# 2. Update the Cloud Run env var:
gcloud run services update "${SERVICE_NAME}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --update-env-vars "GOOGLE_REDIRECT_URI=${SERVICE_URL}/auth/callback"

echo "✅ GOOGLE_REDIRECT_URI updated to ${SERVICE_URL}/auth/callback"
echo "   Now visit ${SERVICE_URL}/auth/login in a browser to verify OAuth flow."


# =============================================================================
# TASK GROUP 6 — Pub/Sub Push Wiring, Verification & Rollback Runbook
# =============================================================================

# --- Task 6.1: Pub/Sub Topic & Push Subscription Configuration -----------

TOPIC_NAME="gmail-notifications"
SUB_NAME="gmail-notifications-sub"

# Ensure topic exists
gcloud pubsub topics create "${TOPIC_NAME}" \
  --project="${PROJECT_ID}" \
  2>/dev/null || echo "Topic already exists."

# Create or update the push subscription
gcloud pubsub subscriptions create "${SUB_NAME}" \
  --topic="${TOPIC_NAME}" \
  --push-endpoint="${SERVICE_URL}/api/v1/webhook/pubsub" \
  --push-auth-service-account="${SA_EMAIL}" \
  --ack-deadline=60 \
  --project="${PROJECT_ID}" \
  2>/dev/null || \
gcloud pubsub subscriptions modify-push-config "${SUB_NAME}" \
  --push-endpoint="${SERVICE_URL}/api/v1/webhook/pubsub" \
  --push-auth-service-account="${SA_EMAIL}" \
  --project="${PROJECT_ID}"

echo "✅ Pub/Sub push subscription wired to ${SERVICE_URL}/api/v1/webhook/pubsub"

# Re-register Gmail mailbox watch via Cloud Run endpoint
curl -sf -X POST "${SERVICE_URL}/api/v1/gmail/watch" | python3 -m json.tool
echo "✅ Gmail mailbox watch registered."

# --- Task 6.2: Run Automated Deployment Verification --------------------

python scripts/verify_phase5_deployment.py --service-url "${SERVICE_URL}"

# --- Task 6.3: Production Rollback Runbook -------------------------------
# List available revisions:
#   gcloud run revisions list --service="${SERVICE_NAME}" --region="${REGION}"
#
# Roll back to a previous revision (zero-downtime traffic shift):
#   PREVIOUS_REVISION="gmail-triage-00001-abc"
#   gcloud run services update-traffic "${SERVICE_NAME}" \
#     --region="${REGION}" \
#     --to-revisions "${PREVIOUS_REVISION}=100"
#
# Verify rollback health:
#   curl -sf "${SERVICE_URL}/health"
#
# Restore traffic to latest revision:
#   gcloud run services update-traffic "${SERVICE_NAME}" \
#     --region="${REGION}" \
#     --to-latest
#
# -----------------------------------------------------------------------
# Secret Rotation:
#   echo -n "NEW_SECRET_VALUE" | \
#     gcloud secrets versions add <SECRET_NAME> --data-file=- --project="${PROJECT_ID}"
#   # Then redeploy or wait for Cloud Run to pick up the new version on next revision.
#
# -----------------------------------------------------------------------
# Log Monitoring:
#   Tail live logs:
#     gcloud run services logs tail "${SERVICE_NAME}" --region="${REGION}"
#   Read recent logs:
#     gcloud run services logs read "${SERVICE_NAME}" --region="${REGION}" --limit=100
#
# -----------------------------------------------------------------------
# Milestone 5 Smoke Test Sequence:
#   1. gcloud run deploy gmail-triage --source . ...
#   2. curl -sf "${SERVICE_URL}/health"
#   3. Visit "${SERVICE_URL}/auth/login" — complete OAuth consent
#   4. Verify "${SERVICE_URL}/api/v1/gmail/status" reports connected email
#   5. Send a test email to the connected Gmail account
#   6. gcloud run services logs read gmail-triage --limit=50
#      → Confirm webhook fired and delta sync completed
#   7. Connect to PostgreSQL and verify new message rows are present
