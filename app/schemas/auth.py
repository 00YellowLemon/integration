"""Pydantic schemas for authentication and OAuth endpoints."""
from pydantic import BaseModel, Field


class AuthLoginResponse(BaseModel):
    """Response containing Google OAuth authorization URL."""
    auth_url: str = Field(..., description="Google OAuth 2.0 authorization URL")


class AuthCallbackResponse(BaseModel):
    """Response returned upon successful OAuth code exchange and credential persistence."""
    status: str = Field(default="success", description="Status indicator")
    email: str = Field(..., description="Authenticated user email address")
    message: str = Field(..., description="Human-readable confirmation message")
