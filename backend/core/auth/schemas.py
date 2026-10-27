from typing import Optional
from pydantic import BaseModel, EmailStr, field_validator


class SignupRequest(BaseModel):
    email: EmailStr
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    # B-007: seconds until the access token expires — lets the frontend schedule
    # a proactive refresh before the token actually expires mid-session.
    expires_in: int = 0
    # B-010: long-lived refresh token; only present on login/signup responses.
    # None means no refresh token was issued (e.g. token refresh responses that
    # return a new access_token but keep the same refresh_token).
    refresh_token: Optional[str] = None


class RefreshRequest(BaseModel):
    """Request body for POST /auth/refresh (B-010)."""
    refresh_token: str


# ── V6.0 User Profile schemas ─────────────────────────────────────────────────

class UserProfileResponse(BaseModel):
    """Response shape for GET /auth/me."""
    user_id: str
    username: str
    email: str
    full_name: Optional[str] = None
    description: Optional[str] = None


class UpdateProfileRequest(BaseModel):
    """Request body for PATCH /auth/profile."""
    full_name:      Optional[str] = None
    description:    Optional[str] = None
    preferred_name: Optional[str] = None   # V6.0 — sets user.name in memory (LLM-editable)

    @field_validator("full_name", "description", "preferred_name", mode="before")
    @classmethod
    def _strip_empty_to_none(cls, v):
        """Convert empty/whitespace-only strings to None."""
        if isinstance(v, str):
            stripped = v.strip()
            return stripped if stripped else None
        return v


class ChangePasswordRequest(BaseModel):
    """Request body for POST /auth/change-password."""
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _validate_new_password(cls, v):
        if len(v) < 8:
            raise ValueError("new_password must be at least 8 characters")
        return v


class SSETokenResponse(BaseModel):
    """Response body for POST /auth/sse-token."""
    sse_token: str
    expires_in: int


class StatusResponse(BaseModel):
    """Bare {"status": "..."} confirmation, e.g. logout / change-password."""
    status: str
