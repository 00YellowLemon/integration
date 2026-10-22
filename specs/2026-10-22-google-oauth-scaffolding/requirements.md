# Feature Requirements: Environment Scaffolding & Google OAuth 2.0 Integration

- **Feature Directory**: `specs/2026-10-22-google-oauth-scaffolding`
- **Roadmap Reference**: [Phase 1 (1.1 - 1.5)](file:///c:/Users/lemon/integration/specs/roadmap.md#L7-L43)
- **Status**: Draft / Pending Implementation
- **Target Branch**: `feature/phase-1-google-oauth-scaffolding`

---

## 1. Executive Summary & Context

To initiate construction of the **Gmail AI Assistant** (defined in [specs/mission.md](file:///c:/Users/lemon/integration/specs/mission.md) and [specs/tech-stack.md](file:///c:/Users/lemon/integration/specs/tech-stack.md)), the system requires a clean application skeleton, secure environment configuration, persistent cloud storage via **Supabase PostgreSQL** (no local Docker required), and an end-to-end Google OAuth 2.0 authorization pipeline.

This feature implements **Phase 1: Environment Scaffolding & Google OAuth 2.0 Integration**, enabling:
1. Environment configuration loading and fail-fast validation via `pydantic-settings`.
2. Secure Google OAuth 2.0 User Authorization Code flow with offline access (`access_type="offline"`, `prompt="consent"`), secure CSRF `state`, and strictly read-only scope (`https://www.googleapis.com/auth/gmail.readonly`).
3. Encrypted persistence of OAuth credentials (`access_token`, `refresh_token`, `token_expiry`) into Supabase PostgreSQL with transparent automatic token refresh.
4. An async `GmailClient` capable of verifying the connection by retrieving user profile statistics (`users.getProfile`) exposed via a dedicated status endpoint (`GET /api/v1/gmail/status`).

---

## 2. In-Scope vs. Out-of-Scope

### In-Scope
- **Application Structure & Layout**: Standardized layout with clean boundaries: `app/core/` (config, security), `app/api/` (routers), `app/models/` (SQLAlchemy models), `app/db/` (Supabase connection and sessions), `app/services/` (OAuth and Gmail clients), `app/schemas/` (Pydantic models), and `tests/`.
- **Dependency Management**: Standard `requirements.txt` specifying pinned, compatible versions: `fastapi`, `uvicorn`, `pydantic`, `pydantic-settings`, `httpx`, `google-auth-oauthlib`, `google-api-python-client`, `sqlalchemy`, `asyncpg`, `cryptography`, and `pytest`.
- **Cloud Database Integration (Supabase PostgreSQL)**:
  - Direct connection to Supabase cloud PostgreSQL using async SQLAlchemy 2.0 and `asyncpg`.
  - No local Docker container dependencies.
  - Creation and management of the `accounts` table for OAuth credential persistence.
- **Configuration Management (`app/core/config.py`)**:
  - `GOOGLE_CLIENT_ID`: OAuth 2.0 Web Client ID from Google Cloud Console.
  - `GOOGLE_CLIENT_SECRET`: OAuth 2.0 Client Secret.
  - `GOOGLE_REDIRECT_URI`: Registered redirect URL (e.g. `http://localhost:8000/auth/callback`).
  - `APP_SECRET_KEY`: Secret key used for cryptographic CSRF state generation and AES-GCM token encryption.
  - `DATABASE_URL`: Supabase PostgreSQL connection string (`postgresql+asyncpg://...`).
  - Fail-fast validation with actionable error feedback if mandatory variables are omitted.
- **Environment Hygiene**: `.env.example` with detailed configuration instructions and updated `.gitignore`.
- **OAuth 2.0 Authorization Flow**:
  - `GET /auth/login`: Generates authorization URL with CSRF protection, requesting `gmail.readonly` and `userinfo.email`.
  - `GET /auth/callback`: Validates CSRF `state`, exchanges authorization code for tokens, and stores them securely.
- **Token Persistence & Auto-Refresh**:
  - Symmetric encryption (AES-GCM via `cryptography`) of access and refresh tokens before storing in Supabase.
  - Automatic refresh logic when access tokens expire or are within 5 minutes of expiry.
- **Gmail Client & Connection Verification**:
  - Async `GmailClient` implementing `get_profile(user_id="me")`.
  - `GET /api/v1/gmail/status`: Returns account email, total message count, thread count, and connection health.

### Out-of-Scope (Deferred to Later Phases)
- Full email ingestion pipeline, MIME parsing, and Pub/Sub push notifications ([Phase 2](file:///c:/Users/lemon/integration/specs/roadmap.md#L45-L78)).
- Gemini 3.8 Flash categorization, triage, and action item extraction ([Phase 3](file:///c:/Users/lemon/integration/specs/roadmap.md#L80-L112)).
- Vector embeddings, semantic search, and inbox digests ([Phase 4](file:///c:/Users/lemon/integration/specs/roadmap.md#L114-L138)).
- Multi-tenant user switching (Phase 1 targets single-user personal Gmail connection).

---

## 3. Architecture & Technical Decisions

1. **Database & Infrastructure Choice**:
   - **Decision**: Use **Supabase Cloud PostgreSQL** directly via `DATABASE_URL` with `asyncpg` driver.
   - **Rationale**: User opted out of local Docker containers. Supabase provides managed PostgreSQL with built-in pgvector readiness for later phases while eliminating local daemon overhead.
2. **Strict Read-Only Enforcement**:
   - **Decision**: Only `https://www.googleapis.com/auth/gmail.readonly` and `https://www.googleapis.com/auth/userinfo.email` scopes will be requested.
   - **Rationale**: Aligns with Constitution Principle 1 (Least Privilege & Read-Only Safety). No write, draft, or delete capabilities will be implemented.
3. **Token Security at Rest**:
   - **Decision**: Encrypt all OAuth tokens stored in PostgreSQL using AES-GCM (via Python `cryptography` library) with `APP_SECRET_KEY`.
   - **Rationale**: Plaintext OAuth refresh tokens grant persistent access to the user's mailbox; encrypting them ensures database-level leaks do not compromise the Gmail account.
4. **CSRF State Protection**:
   - **Decision**: State tokens generated during `/auth/login` will contain a cryptographically signed HMAC timestamp and nonce.
   - **Rationale**: Protects against OAuth CSRF interception attacks during the browser redirect loop without requiring server-side session cookies.
5. **Token Auto-Refresh Lifecycle**:
   - **Decision**: The credentials provider will check `token_expiry`. If expired or `< 300` seconds remaining, refresh using `google.auth.transport.requests.Request` and update the database row.
   - **Rationale**: Ensures headless and background workers never fail on expired short-lived access tokens.

---

## 4. Key Schemas & Models

### Configuration Schema (`app/core/config.py`)
```python
class Settings(BaseSettings):
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: SecretStr
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/auth/callback"
    APP_SECRET_KEY: SecretStr
    DATABASE_URL: str
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
```

### Relational Model: `accounts` (`app/models/account.py`)
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | UUID | Primary Key, default `uuid4` | Unique account identifier |
| `email` | String(255) | Unique, Index, Not Null | Authenticated Google email address |
| `encrypted_access_token` | Text | Not Null | AES-GCM encrypted OAuth access token |
| `encrypted_refresh_token` | Text | Not Null | AES-GCM encrypted OAuth refresh token |
| `token_expiry` | DateTime | Not Null | Access token expiration timestamp (UTC) |
| `history_id` | String(64) | Nullable | Gmail mailbox checkpoint sync cursor |
| `is_active` | Boolean | Default True | Flag indicating active connection status |
| `created_at` | DateTime | Default UTC now | Record creation time |
| `updated_at` | DateTime | Default UTC now, onupdate | Record update time |

### API Response Schemas (`app/schemas/auth.py` & `app/schemas/gmail.py`)
- `AuthLoginResponse`: `auth_url: str`
- `AuthCallbackResponse`: `status: str`, `email: str`, `message: str`
- `GmailStatusResponse`: `email: str`, `messages_total: int`, `threads_total: int`, `history_id: str`, `is_connected: bool`
