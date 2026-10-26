# Production Operations & Rollback Runbook: Google Cloud Run Deployment

- **Feature Directory**: `specs/2026-10-26-cloud-run-deployment`
- **Roadmap Reference**: [Phase 5 (5.1 - 5.7)](file:///c:/Users/lemon/integration/specs/roadmap.md#L147-L201)
- **Service Name**: `gmail-triage`
- **Region**: `us-central1`
- **GCP Project**: `project-cd2fe81b-1cc9-4618-a35`
- **Service Account**: `gmail-triage-runner@project-cd2fe81b-1cc9-4618-a35.iam.gserviceaccount.com`
- **Automation Companion Script**: [`scripts/provision_phase5_cloud_run.sh`](file:///c:/Users/lemon/integration/scripts/provision_phase5_cloud_run.sh)
- **Live Verification Companion Script**: [`scripts/verify_phase5_deployment.py`](file:///c:/Users/lemon/integration/scripts/verify_phase5_deployment.py)

---

## 1. Prerequisites & GCP Authentication

Confirm that `gcloud` CLI is installed and targeting the correct active account and project:

```powershell
gcloud config set project project-cd2fe81b-1cc9-4618-a35
gcloud auth list
```

Expected output:
- Active account: `annhyko@gmail.com`
- Active project: `project-cd2fe81b-1cc9-4618-a35`

---

## 2. GCP Infrastructure & IAM Provisioning

### 2.1 Enable Required GCP APIs

Execute in bash or powershell:
```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  pubsub.googleapis.com \
  aiplatform.googleapis.com \
  --project=project-cd2fe81b-1cc9-4618-a35
```

### 2.2 Provision Dedicated Service Account

Create the runtime identity:
```bash
gcloud iam service-accounts create gmail-triage-runner \
  --description="Dedicated runtime identity for Gmail AI Triage Cloud Run service" \
  --display-name="Gmail Triage Cloud Run Runner" \
  --project=project-cd2fe81b-1cc9-4618-a35
```

Grant least-privilege IAM roles:
```bash
for role in roles/secretmanager.secretAccessor roles/pubsub.publisher roles/aiplatform.user; do
  gcloud projects add-iam-policy-binding project-cd2fe81b-1cc9-4618-a35 \
    --member="serviceAccount:gmail-triage-runner@project-cd2fe81b-1cc9-4618-a35.iam.gserviceaccount.com" \
    --role="$role" \
    --condition=None \
    --quiet
done
```

---

## 3. Secret Manager Configuration & Separation

Sensitive credentials must never reside in source code, Dockerfiles, or git history.

### 3.1 Provision Secrets

Create secrets in Google Secret Manager and populate initial values from your local secure store:

```bash
for secret in GOOGLE_CLIENT_SECRET APP_SECRET_KEY DATABASE_URL; do
  gcloud secrets create "$secret" \
    --replication-policy=automatic \
    --project=project-cd2fe81b-1cc9-4618-a35
done
```

Add current values via standard input (interactive):
```bash
echo -n "<YOUR_GOOGLE_CLIENT_SECRET>" | gcloud secrets versions add GOOGLE_CLIENT_SECRET --data-file=-
echo -n "<YOUR_APP_SECRET_KEY>" | gcloud secrets versions add APP_SECRET_KEY --data-file=-
echo -n "<YOUR_DATABASE_URL>" | gcloud secrets versions add DATABASE_URL --data-file=-
```

Verify secret access:
```bash
gcloud secrets versions access latest --secret=DATABASE_URL
```

### 3.2 Secret vs Non-Secret Environment Division

| Setting | Type | Target Injection |
|---|---|---|
| `GOOGLE_CLIENT_SECRET` | Secret | Cloud Run `--set-secrets` (`GOOGLE_CLIENT_SECRET:latest`) |
| `APP_SECRET_KEY` | Secret | Cloud Run `--set-secrets` (`APP_SECRET_KEY:latest`) |
| `DATABASE_URL` | Secret | Cloud Run `--set-secrets` (`DATABASE_URL:latest`) |
| `GOOGLE_CLIENT_ID` | Plain Var | Cloud Run `--set-env-vars` |
| `GCP_PROJECT_ID` | Plain Var | Cloud Run `--set-env-vars` (`project-cd2fe81b-1cc9-4618-a35`) |
| `PUBSUB_TOPIC_NAME` | Plain Var | Cloud Run `--set-env-vars` (`gmail-notifications`) |
| `PUBSUB_SUBSCRIPTION_NAME` | Plain Var | Cloud Run `--set-env-vars` (`gmail-notifications-sub`) |
| `GOOGLE_CLOUD_LOCATION` | Plain Var | Cloud Run `--set-env-vars` (`global`) |
| `LOG_LEVEL` | Plain Var | Cloud Run `--set-env-vars` (`INFO`) |
| `TRIAGE_ENABLED` | Plain Var | Cloud Run `--set-env-vars` (`true`) |

---

## 4. Cloud Run Source Deployment (Procfile, No Docker)

Deploy directly from repository root using Google Cloud Buildpacks:

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
  --set-env-vars GOOGLE_CLIENT_ID=<YOUR_CLIENT_ID>,GCP_PROJECT_ID=project-cd2fe81b-1cc9-4618-a35,PUBSUB_TOPIC_NAME=gmail-notifications,PUBSUB_SUBSCRIPTION_NAME=gmail-notifications-sub,GOOGLE_CLOUD_LOCATION=global,TRIAGE_ENABLED=true
```

Upon successful deployment, capture the Service URL:
```bash
SERVICE_URL=$(gcloud run services describe gmail-triage --region=us-central1 --format="value(status.url)")
echo "Deployed Service URL: $SERVICE_URL"
```

---

## 5. OAuth 2.0 Redirect Cutover

1. Open Google Cloud Console:
   `https://console.cloud.google.com/apis/credentials`
2. Select your OAuth 2.0 Web Client ID.
3. Under **Authorized redirect URIs**, click **Add URI** and append:
   `https://<SERVICE_URL>/auth/callback`
4. Save the configuration.
5. Update the Cloud Run environment variable:
   ```bash
   gcloud run services update gmail-triage \
     --region us-central1 \
     --update-env-vars GOOGLE_REDIRECT_URI=${SERVICE_URL}/auth/callback
   ```
6. Verify interactive login by opening `${SERVICE_URL}/auth/login` in your web browser.

---

## 6. Pub/Sub Push Subscription Wiring

1. Ensure the Pub/Sub topic exists:
   ```bash
   gcloud pubsub topics create gmail-notifications \
     --project=project-cd2fe81b-1cc9-4618-a35 2>/dev/null || true
   ```

2. Create or update the push subscription pointing to the Cloud Run webhook endpoint:
   ```bash
   gcloud pubsub subscriptions create gmail-notifications-sub \
     --topic=gmail-notifications \
     --push-endpoint="${SERVICE_URL}/api/v1/webhook/pubsub" \
     --push-auth-service-account=gmail-triage-runner@project-cd2fe81b-1cc9-4618-a35.iam.gserviceaccount.com \
     --ack-deadline=60 \
     --project=project-cd2fe81b-1cc9-4618-a35 2>/dev/null || \
   gcloud pubsub subscriptions modify-push-config gmail-notifications-sub \
     --push-endpoint="${SERVICE_URL}/api/v1/webhook/pubsub" \
     --push-auth-service-account=gmail-triage-runner@project-cd2fe81b-1cc9-4618-a35.iam.gserviceaccount.com \
     --project=project-cd2fe81b-1cc9-4618-a35
   ```

3. Re-register the Gmail mailbox watch:
   ```bash
   curl -X POST "${SERVICE_URL}/api/v1/gmail/watch"
   ```

---

## 7. Automated Live Verification

Run the automated live verification script against the deployment:

```powershell
python scripts/verify_phase5_deployment.py --service-url "https://<SERVICE_URL>"
```

Checks performed:
1. Probe `/health` (verifies HTTP 200 and database connection status).
2. Probe `/api/v1/digest/today` (verifies digest endpoints and views).
3. Test unknown account payload on `/api/v1/webhook/pubsub` (expects HTTP 404).
4. Probe `/docs` (verifies OpenAPI UI).
5. Send malformed JSON payload on webhook (expects HTTP 400).

---

## 8. Observability & Log Monitoring

### Live Log Streaming
```bash
gcloud run services logs tail gmail-triage --region=us-central1
```

### Read Recent Logs
```bash
gcloud run services logs read gmail-triage --region=us-central1 --limit=100
```

### Filter Logs by Severity / Webhook
```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=gmail-triage AND textPayload:webhook" --limit=20
```

---

## 9. Zero-Downtime Rollback & Secret Rotation

### Instant Traffic Shift Rollback
If a newly deployed revision encounters runtime issues:

1. List available revisions:
   ```bash
   gcloud run revisions list --service=gmail-triage --region=us-central1
   ```

2. Shift 100% traffic to the known-good previous revision:
   ```bash
   gcloud run services update-traffic gmail-triage \
     --region us-central1 \
     --to-revisions <PREVIOUS_REVISION_NAME>=100
   ```

3. Verify health on the restored revision:
   ```bash
   curl -s "https://<SERVICE_URL>/health"
   ```

4. Once the issue is resolved and a fix deployed, restore traffic to the latest revision:
   ```bash
   gcloud run services update-traffic gmail-triage \
     --region us-central1 \
     --to-latest
   ```

### Secret Rotation Procedure
To rotate a secret (e.g. `APP_SECRET_KEY` or `DATABASE_URL`):

1. Add the new version in Secret Manager:
   ```bash
   echo -n "<NEW_SECRET_VALUE>" | \
     gcloud secrets versions add <SECRET_NAME> \
       --data-file=- \
       --project=project-cd2fe81b-1cc9-4618-a35
   ```

2. Cloud Run automatically pins `:latest` secrets to the latest version on each new revision. Trigger a new revision deployment:
   ```bash
   gcloud run services update gmail-triage --region us-central1
   ```

3. Verify service health post-rotation.
