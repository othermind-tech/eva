import secrets
from logging import getLogger
from passlib.context import CryptContext
from jose import jwt

from core.auth.signing_key import get_signing_key
from datetime import datetime, timedelta, timezone

from config.settings import (
    ALGORITHM,
    TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
)

logger = getLogger(__name__)

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash password using bcrypt."""
    logger.debug("Hashing password")
    return pwd_ctx.hash(password)


def verify_password(password: str, hash: str) -> bool:
    """Verify password against hash."""
    logger.debug("Verifying password")
    return pwd_ctx.verify(password, hash)


def create_access_token(user_id: str, session_id: str):
    """Create JWT access token."""
    payload = {
        "sub": user_id,
        "sid": session_id,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=TOKEN_EXPIRE_MINUTES),
    }
    logger.debug(
        "Creating access token",
        extra={
            "user_id": user_id,
            "session_id": session_id,
            "expires_in_minutes": TOKEN_EXPIRE_MINUTES,
        },
    )
    return jwt.encode(payload, get_signing_key(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """
    Verify an access token's signature and expiry and return its claims.
    Raises jose.JWTError if it is invalid. The one place tokens are verified,
    so the signing key is only ever obtained through get_signing_key().
    """
    return jwt.decode(token, get_signing_key(), algorithms=[ALGORITHM])


def session_matches_user(session, user_id: str) -> bool:
    """
    True when `session` (an AuthSession row) belongs to the user the token
    claims to be (`sub`). Verification already requires a live session for the
    token's `sid`; without this check any live session -- of ANY user, and
    anyone can create one by signing up -- would back a token naming a
    different user, so a leaked signing key alone was enough to impersonate
    anyone.
    """
    return bool(session) and getattr(session, "user_id", None) == user_id


def create_refresh_token() -> str:
    """
    Generate a cryptographically secure refresh token. (B-010)
    Returns a URL-safe base64 string (48 random bytes → 64 chars).
    """
    return secrets.token_urlsafe(48)
