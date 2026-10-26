"""OAuth 2.0 authentication endpoints: login redirect and callback token exchange."""
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import generate_oauth_state, verify_oauth_state
from app.db.session import get_db
from app.schemas.auth import AuthCallbackResponse, AuthLoginResponse
from app.services.auth_service import READONLY_SCOPES
from app.services.token_store import TokenStore

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _build_oauth_flow(state: Optional[str] = None) -> Flow:
    """Helper to construct google-auth Flow instance."""
    settings = get_settings()
    client_config = {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET.get_secret_value(),
            "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    return Flow.from_client_config(
        client_config=client_config,
        scopes=READONLY_SCOPES,
        redirect_uri=settings.GOOGLE_REDIRECT_URI,
        state=state,
    )


async def fetch_user_email(access_token: str) -> str:
    """Fetch user email address from Google OAuth userinfo endpoint."""
    async with httpx.AsyncClient() as client:
        userinfo_resp = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        if userinfo_resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to retrieve user info from Google: {userinfo_resp.text}",
            )
        userinfo = userinfo_resp.json()
        email = userinfo.get("email")
        if not email:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Google account user info did not contain an email address.",
            )
        return email


@router.get(
    "/login",
    response_model=AuthLoginResponse,
    summary="Initiate Google OAuth 2.0 authorization",
)
async def auth_login(
    redirect: bool = Query(
        default=False,
        description="If True, directly redirects browser to Google OAuth consent screen",
    )
):
    """
    Generate Google OAuth 2.0 consent URL with CSRF protection.
    Returns auth_url JSON or performs HTTP 307 redirect if ?redirect=true.
    """
    settings = get_settings()
    secret_key = settings.APP_SECRET_KEY.get_secret_value()
    csrf_state = generate_oauth_state(secret_key)

    flow = _build_oauth_flow(state=csrf_state)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )

    if redirect:
        return RedirectResponse(url=auth_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    return AuthLoginResponse(auth_url=auth_url)


@router.get(
    "/callback",
    response_model=AuthCallbackResponse,
    summary="Google OAuth 2.0 callback and code exchange",
)
async def auth_callback(
    request: Request,
    code: Optional[str] = Query(default=None, description="Google authorization code"),
    state: Optional[str] = Query(default=None, description="CSRF state parameter"),
    error: Optional[str] = Query(default=None, description="OAuth error code if authorization failed"),
    db: AsyncSession = Depends(get_db),
):
    """
    Handle Google OAuth 2.0 callback, validate CSRF state, exchange code for
    tokens, fetch user email, and persist encrypted credentials to Supabase.
    """
    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth authorization failed or was denied: {error}",
        )

    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required OAuth query parameters: 'code' and 'state' are required.",
        )

    settings = get_settings()
    secret_key = settings.APP_SECRET_KEY.get_secret_value()

    # Validate CSRF state
    if not verify_oauth_state(state, secret_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired OAuth CSRF state token.",
        )

    # Exchange authorization code for tokens
    flow = _build_oauth_flow(state=state)
    try:
        flow.fetch_token(code=code)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to exchange authorization code for tokens: {str(e)}",
        )

    credentials = flow.credentials
    access_token = credentials.token
    refresh_token = credentials.refresh_token

    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google OAuth exchange completed without access token.",
        )

    # Fetch user email address using the access token
    email = await fetch_user_email(access_token)

    # Determine token expiry timestamp
    if credentials.expiry:
        if credentials.expiry.tzinfo is None:
            expiry = credentials.expiry.replace(tzinfo=timezone.utc)
        else:
            expiry = credentials.expiry
    else:
        expiry = datetime.now(timezone.utc) + timedelta(seconds=3600)

    # Encrypt and persist to database
    await TokenStore.save_tokens(
        db=db,
        email=email,
        access_token=access_token,
        refresh_token=refresh_token or "",
        expiry=expiry,
    )

    return AuthCallbackResponse(
        status="success",
        email=email,
        message="Successfully authenticated with Google and stored encrypted credentials.",
    )
