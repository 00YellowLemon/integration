# Validation & Testing Guide: Environment Scaffolding & Google OAuth 2.0 Integration

- **Feature Directory**: `specs/2026-10-22-google-oauth-scaffolding`
- **Target Branch**: `feature/phase-1-google-oauth-scaffolding`
- **Specification Reference**: [requirements.md](file:///c:/Users/lemon/integration/specs/2026-10-22-google-oauth-scaffolding/requirements.md) | [plan.md](file:///c:/Users/lemon/integration/specs/2026-10-22-google-oauth-scaffolding/plan.md)
- **Testing Approach**: Dual-Layer Verification (Full Automated Pytest Suite with Mocks + Interactive Manual Verification CLI & Browser Walkthrough)

---

## 1. Prerequisites Checklist

Before running validation tests, confirm the following prerequisites:

- [ ] Python 3.11+ installed.
- [ ] Virtual environment activated and dependencies installed:
  ```bash
  python -m venv .venv
  .venv\Scripts\activate   # On Windows
  pip install -r requirements.txt
  ```
- [ ] `.env` file created from `.env.example` with valid credentials:
  - `GOOGLE_CLIENT_ID`: OAuth 2.0 Web Client ID from Google Cloud Console.
  - `GOOGLE_CLIENT_SECRET`: OAuth 2.0 Client Secret.
  - `GOOGLE_REDIRECT_URI`: `http://localhost:8000/auth/callback` (must match Google Console URI).
  - `APP_SECRET_KEY`: Random 32-byte secret (e.g. generated via `openssl rand -hex 32`).
  - `DATABASE_URL`: Supabase PostgreSQL async URL (`postgresql+asyncpg://postgres:[YOUR-PASSWORD]@[YOUR-HOST]:5432/postgres`).

---

## 2. Layer 1: Automated Pytest Suite (CI-Ready)

The automated test suite runs completely with mocked Google endpoints and a test database (or SQLite async / mocked session), requiring no live network calls or credentials to pass.

### Test Execution Command
```bash
pytest tests/ -v
```

### 2.1 Configuration & Startup Tests (`tests/test_config.py`)
- [ ] **Test Missing Config**: Running app with missing `GOOGLE_CLIENT_ID` or `DATABASE_URL` raises `pydantic.ValidationError` and exits cleanly.
- [ ] **Test Valid Config**: All configuration fields parse correctly; secrets are masked when logged.

### 2.2 Security & CSRF State Tests (`tests/test_auth.py`)
- [ ] **Test State Generation**: `generate_oauth_state(secret_key)` produces unique signed state strings.
- [ ] **Test State Verification**:
  - Valid state within time window returns `True`.
  - Expired state (> 600s) returns `False`.
  - Tampered state string returns `False`.
- [ ] **Test Token Encryption / Decryption**:
  - `encrypt_token(raw_token, key)` produces non-deterministic ciphertext (AES-GCM random nonce).
  - `decrypt_token(cipher, key)` accurately recovers original plaintext token.
  - Attempting to decrypt with an incorrect key raises decryption error.

### 2.3 OAuth Flow Endpoint Tests (`tests/test_auth.py`)
- [ ] **Test `GET /auth/login`**:
  - Returns HTTP 200 with JSON `{"auth_url": "..."}` containing `accounts.google.com/o/oauth2/v2/auth`.
  - URL query includes `scope` containing `gmail.readonly` and `userinfo.email`.
  - URL query includes `access_type=offline`, `prompt=consent`, and valid `state`.
- [ ] **Test `GET /auth/callback` with Invalid State**:
  - Passing `?code=mock_code&state=bad_state` returns HTTP 403 Forbidden.
- [ ] **Test `GET /auth/callback` Successful Exchange**:
  - Mock Google token endpoint returning access token and refresh token.
  - Callback persists encrypted tokens into `Account` table.
  - Returns HTTP 200 with confirmation message.

### 2.4 Gmail Client & Status Endpoint Tests (`tests/test_gmail.py`)
- [ ] **Test `GmailClient.get_profile()`**:
  - Mock `users.getProfile` returning `{"emailAddress": "test@gmail.com", "messagesTotal": 1420, "threadsTotal": 850, "historyId": "987654"}`.
  - Client extracts and formats profile dictionary accurately.
- [ ] **Test `GET /api/v1/gmail/status`**:
  - Returns HTTP 200 with `GmailStatusResponse`:
    ```json
    {
      "email": "test@gmail.com",
      "messages_total": 1420,
      "threads_total": 850,
      "history_id": "987654",
      "is_connected": true
    }
    ```
- [ ] **Test Auto-Refresh Mechanism**:
  - Insert expired token in test database.
  - Calling status endpoint automatically triggers mock refresh exchange and updates DB with new expiration timestamp without throwing authentication error.

---

## 3. Layer 2: Live Verification with Supabase & Google OAuth

This walkthrough verifies end-to-end functionality using real Supabase PostgreSQL and Google Cloud Console OAuth credentials.

### Step 3.1: Verify Supabase Database Table Creation
1. Start the FastAPI server:
   ```bash
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
   ```
2. Verify startup logs indicate successful connection to Supabase and creation of table `accounts`.
3. Test root healthcheck:
   ```bash
   curl -i http://127.0.0.1:8000/health
   ```
- [ ] **HTTP Status**: 200 OK
- [ ] **Response**: `{"status": "ok", "database": "connected"}`

### Step 3.2: Interactive Browser OAuth Handshake
1. Open browser to:
   ```
   http://127.0.0.1:8000/auth/login?redirect=true
   ```
   *(Or navigate to the URL provided by `GET /auth/login`)*.
2. Google OAuth consent screen displays:
   - Requesting permission: **"View your email messages and settings"** (`gmail.readonly`).
   - Requesting permission: **"See your primary Google Account email address"**.
3. Complete the authorization with your test Gmail account.
4. Google redirects to:
   ```
   http://localhost:8000/auth/callback?code=...&state=...
   ```
- [ ] **HTTP Status**: 200 OK.
- [ ] **Browser Displays**: Confirmation that Google account was successfully connected.
- [ ] **Database Verification**: Check Supabase SQL Editor:
  ```sql
  SELECT email, token_expiry, is_active, created_at FROM accounts;
  ```
  - Record exists with authenticated email.
  - `encrypted_access_token` and `encrypted_refresh_token` are non-empty ciphertexts.

### Step 3.3: Gmail Connection Verification (`GET /api/v1/gmail/status`)
Run curl or open in browser:
```bash
curl -i http://127.0.0.1:8000/api/v1/gmail/status
```
- [ ] **HTTP Status**: 200 OK.
- [ ] **Response Body**:
  ```json
  {
    "email": "user@gmail.com",
    "messages_total": 1250,
    "threads_total": 820,
    "history_id": "12345678",
    "is_connected": true
  }
  ```
- [ ] Verify that `messages_total` and `threads_total` correspond to the actual Gmail mailbox.

### Step 3.4: Verify Token Auto-Refresh Lifecycle
1. In Supabase SQL Editor, artificially backdate the token expiration for your account:
   ```sql
   UPDATE accounts SET token_expiry = NOW() - INTERVAL '1 hour';
   ```
2. Query the status endpoint again:
   ```bash
   curl -i http://127.0.0.1:8000/api/v1/gmail/status
   ```
- [ ] **HTTP Status**: 200 OK (no 401 Unauthorized returned).
- [ ] **Database Verification**: Re-run `SELECT token_expiry FROM accounts;`:
  - `token_expiry` has been automatically updated to a future time (+3600 seconds from now).

### Step 3.5: Run Interactive Verification CLI Tool
Run the included verification CLI script:
```bash
python scripts/verify_oauth.py
```
- [ ] Script confirms Supabase connection.
- [ ] Script confirms active OAuth account.
- [ ] Script fetches and prints live Gmail statistics in formatted table.

---

## 4. Merge Readiness Checklist

Before merging branch `feature/phase-1-google-oauth-scaffolding` into `main`:
- [ ] All tests in `tests/` pass cleanly (`pytest -v`).
- [ ] Layer 2 manual verification completed with live Gmail account.
- [ ] `.env` and `.tokens.json` files are excluded from git commit (`git status` clean).
- [ ] Strictly read-only assurance: zero write/send endpoints exist in the codebase.
- [ ] Code is formatted and compliant with PEP 8.
- [ ] PR description references [requirements.md](file:///c:/Users/lemon/integration/specs/2026-10-22-google-oauth-scaffolding/requirements.md) and [plan.md](file:///c:/Users/lemon/integration/specs/2026-10-22-google-oauth-scaffolding/plan.md).
