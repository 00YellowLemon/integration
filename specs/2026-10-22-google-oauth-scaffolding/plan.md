# Implementation Plan: Environment Scaffolding & Google OAuth 2.0 Integration

- **Feature Directory**: `specs/2026-10-22-google-oauth-scaffolding`
- **Target Branch**: `feature/phase-1-google-oauth-scaffolding`
- **Specification Reference**: [requirements.md](file:///c:/Users/lemon/integration/specs/2026-10-22-google-oauth-scaffolding/requirements.md)
- **Constitution Guidance**: [specs/mission.md](file:///c:/Users/lemon/integration/specs/mission.md) | [specs/tech-stack.md](file:///c:/Users/lemon/integration/specs/tech-stack.md) | [specs/roadmap.md](file:///c:/Users/lemon/integration/specs/roadmap.md#L7-L43)

---

## Task Group 1: Project Skeleton & Configuration Management

- [ ] **Task 1.1: Initialize Project Directory Layout**
  - Scaffold modular layout conforming to FastAPI best practices:
    ```
    app/
      ├── __init__.py
      ├── main.py
      ├── core/
      │   ├── __init__.py
      │   ├── config.py
      │   └── security.py
      ├── db/
      │   ├── __init__.py
      │   ├── session.py
      │   └── base.py
      ├── models/
      │   ├── __init__.py
      │   └── account.py
      ├── schemas/
      │   ├── __init__.py
      │   ├── auth.py
      │   └── gmail.py
      ├── api/
      │   ├── __init__.py
      │   ├── router.py
      │   ├── auth.py
      │   └── gmail.py
      └── services/
          ├── __init__.py
          ├── token_store.py
          ├── auth_service.py
          └── gmail.py
    tests/
      ├── __init__.py
      ├── conftest.py
      ├── test_config.py
      ├── test_auth.py
      └── test_gmail.py
    scripts/
      └── verify_oauth.py
    ```
  - Ensure all packages have valid `__init__.py`.

- [ ] **Task 1.2: Define Dependencies in `requirements.txt`**
  - Pin the following production and testing dependencies:
    - `fastapi>=0.110.0,<1.0.0`
    - `uvicorn[standard]>=0.29.0,<1.0.0`
    - `pydantic>=2.7.0,<3.0.0`
    - `pydantic-settings>=2.2.0,<3.0.0`
    - `httpx>=0.27.0,<1.0.0`
    - `google-auth>=2.29.0,<3.0.0`
    - `google-auth-oauthlib>=1.2.0,<2.0.0`
    - `google-api-python-client>=2.125.0,<3.0.0`
    - `sqlalchemy>=2.0.29,<3.0.0`
    - `asyncpg>=0.29.0,<1.0.0`
    - `cryptography>=42.0.5,<43.0.0`
    - `python-dotenv>=1.0.1`
    - `pytest>=8.1.1,<9.0.0`
    - `pytest-asyncio>=0.23.6,<1.0.0`

- [ ] **Task 1.3: Implement Configuration Management (`app/core/config.py`)**
  - Implement `Settings` class using `pydantic_settings.BaseSettings`:
    - `GOOGLE_CLIENT_ID: str`
    - `GOOGLE_CLIENT_SECRET: SecretStr`
    - `GOOGLE_REDIRECT_URI: str = "http://localhost:8000/auth/callback"`
    - `APP_SECRET_KEY: SecretStr`
    - `DATABASE_URL: str` (Supabase PostgreSQL async connection string)
    - `APP_HOST: str = "127.0.0.1"`
    - `APP_PORT: int = 8000`
    - `LOG_LEVEL: str = "INFO"`
  - Configure `.env` auto-reading (`SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")`).
  - Add cached singleton `get_settings()`.

- [ ] **Task 1.4: Provide Environment Template (`.env.example`) & `.gitignore`**
  - Create `.env.example` documenting all configuration keys with instructions for Google Cloud Console and Supabase.
  - Ensure `.gitignore` covers `.env`, `.env.*`, `__pycache__/`, `.pytest_cache/`, `*.tokens.json`, and virtual environment directories.

---

## Task Group 2: Google OAuth 2.0 Authorization Flow & Security

- [ ] **Task 2.1: Implement CSRF State Manager & Secret Utilities (`app/core/security.py`)**
  - Implement HMAC-SHA256 based CSRF state generator: `generate_oauth_state(secret_key: str) -> str`.
  - Implement validation helper: `verify_oauth_state(state: str, secret_key: str, max_age_seconds: int = 600) -> bool`.
  - Implement AES-GCM encryption and decryption helpers:
    - `encrypt_token(plain_token: str, secret_key: str) -> str`
    - `decrypt_token(cipher_text: str, secret_key: str) -> str`

- [ ] **Task 2.2: Implement OAuth Consent Initiator (`GET /auth/login`)**
  - In `app/api/auth.py`, implement `GET /auth/login`:
    - Generate secure state via `generate_oauth_state`.
    - Construct Google OAuth 2.0 authorization URL using `google_auth_oauthlib.flow.Flow` (or manual OAuth endpoint construction) with:
      - Client ID from config.
      - Scopes: `https://www.googleapis.com/auth/gmail.readonly` and `https://www.googleapis.com/auth/userinfo.email`.
      - Parameters: `access_type="offline"`, `prompt="consent"`, `include_granted_scopes="true"`.
    - Support both JSON response (`{"auth_url": "..."}`) for API consumers and HTTP 307 Redirect for direct browser visits via optional `?redirect=true` query param.

- [ ] **Task 2.3: Implement OAuth Callback & Code Exchange (`GET /auth/callback`)**
  - In `app/api/auth.py`, implement `GET /auth/callback`:
    - Accept query parameters: `code: str`, `state: str`, and optional `error: str`.
    - Handle OAuth errors (`error=access_denied`) gracefully.
    - Validate `state` using `verify_oauth_state`; raise HTTP 403 on invalid/expired state.
    - Exchange authorization `code` for Google credentials dictionary (access token, refresh token, token expiry, id_token).
    - Parse user email from `id_token` or Google userinfo endpoint.
    - Persist encrypted tokens to Supabase via `TokenStore`.
    - Return clean confirmation message (HTTP 200).

---

## Task Group 3: Token Store, Supabase Persistence & Auto-Refresh

- [ ] **Task 3.1: Configure Supabase Database Session (`app/db/session.py` & `app/db/base.py`)**
  - Setup SQLAlchemy 2.0 async engine and session factory (`async_sessionmaker`) bound to `settings.DATABASE_URL`.
  - Provide FastAPI dependency `get_db() -> AsyncGenerator[AsyncSession, None]`.
  - Implement `init_db()` to auto-create tables if they do not exist (idempotent `CREATE TABLE IF NOT EXISTS`).

- [ ] **Task 3.2: Implement `Account` Model (`app/models/account.py`)**
  - Define declarative SQLAlchemy model `Account`:
    - `id`: UUID (Primary Key)
    - `email`: String(255), unique, indexed
    - `encrypted_access_token`: Text, not null
    - `encrypted_refresh_token`: Text, not null
    - `token_expiry`: DateTime (UTC), not null
    - `history_id`: String(64), nullable
    - `is_active`: Boolean, default True
    - `created_at`, `updated_at`: DateTime (UTC)

- [ ] **Task 3.3: Build Token Persistence Service (`app/services/token_store.py`)**
  - Implement `TokenStore` with methods:
    - `save_tokens(db: AsyncSession, email: str, access_token: str, refresh_token: str, expiry: datetime) -> Account` (upserts by email).
    - `get_active_account(db: AsyncSession, email: Optional[str] = None) -> Optional[Account]`.
    - `update_tokens(db: AsyncSession, account_id: UUID, access_token: str, expiry: datetime) -> None`.

- [ ] **Task 3.4: Implement Auto-Refresh Credentials Provider (`app/services/auth_service.py`)**
  - Build `get_valid_credentials(db: AsyncSession, email: Optional[str] = None) -> google.oauth2.credentials.Credentials`:
    - Fetch active account from `TokenStore`.
    - Decrypt stored tokens.
    - Instantiate `google.oauth2.credentials.Credentials`.
    - Check if `credentials.expired` or expires within 5 minutes.
    - If expired, refresh synchronously using `credentials.refresh(google.auth.transport.requests.Request())`.
    - Save refreshed `access_token` and new `expiry` back to Supabase.
    - Return ready-to-use authenticated `Credentials` object.

---

## Task Group 4: Gmail API Client & Status Endpoint

- [ ] **Task 4.1: Build Async-Compatible `GmailClient` (`app/services/gmail.py`)**
  - Implement `GmailClient` class initialized with valid `Credentials`.
  - Enforce strictly read-only execution:
    - Verify credentials scopes only include `gmail.readonly`.
    - Raise error if any write scope is detected.
  - Implement `get_profile(user_id: str = "me") -> dict`:
    - Execute Gmail REST API call `users.getProfile` (`GET https://gmail.googleapis.com/gmail/v1/users/me/profile`).
    - Extract `emailAddress`, `messagesTotal`, `threadsTotal`, and `historyId`.

- [ ] **Task 4.2: Implement Status Endpoint (`GET /api/v1/gmail/status`)**
  - In `app/api/gmail.py`, create router endpoint `GET /api/v1/gmail/status`.
  - Inject database session and obtain valid Google credentials.
  - Call `GmailClient.get_profile()`.
  - Return typed `GmailStatusResponse`:
    - `email`: Authenticated email address
    - `messages_total`: Total message count in mailbox
    - `threads_total`: Total thread count in mailbox
    - `history_id`: Current mailbox sync checkpoint cursor
    - `is_connected`: True

- [ ] **Task 4.3: Assemble Application & Router Mounting (`app/main.py`)**
  - Assemble FastAPI application with metadata, CORS configuration, and lifespan context.
  - Mount routers:
    - `/auth` -> `app.api.auth.router`
    - `/api/v1/gmail` -> `app.api.gmail.router`
  - Add root health check endpoint `GET /health` verifying app and Supabase connectivity.

---

## Task Group 5: Milestone 1 Integration & Testing Suite

- [ ] **Task 5.1: Build Automated Unit & Integration Tests (`tests/`)**
  - `tests/test_config.py`: Test settings loading, default values, and fail-fast behavior on missing required env vars.
  - `tests/test_auth.py`:
    - Test CSRF state generation, validation, and expiration.
    - Test AES-GCM token encryption/decryption round-trip.
    - Test `/auth/login` endpoint returns valid Google consent URL with correct scopes and parameters.
    - Test `/auth/callback` handles code exchange and persistence with mocked Google OAuth transport.
  - `tests/test_gmail.py`:
    - Test `GmailClient.get_profile` with mocked Gmail API response.
    - Test `/api/v1/gmail/status` returns HTTP 200 with accurate profile statistics.
    - Test auto-refresh mechanism updates expired tokens automatically.

- [ ] **Task 5.2: Create Interactive Verification Script (`scripts/verify_oauth.py`)**
  - Build standalone CLI script allowing developer to run interactive live connection test:
    - Check environment variables and Supabase connection.
    - Output clickable OAuth login URL.
    - Wait for callback or poll Supabase for stored account.
    - Fetch and display Gmail mailbox statistics (`messagesTotal`, `threadsTotal`, `historyId`).
    - Verify token auto-refresh cycle.

- [ ] **Task 5.3: Milestone 1 End-to-End Handshake Verification**
  - Execute full test suite (`pytest -v`).
  - Run manual walkthrough against real Gmail account to confirm Milestone 1 completion.
