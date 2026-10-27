from fastapi import Depends, HTTPException, status
from logging import getLogger
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from datetime import datetime, timezone
from core.db.session import AsyncSessionLocal
from core.db.models import AuthSession
from core.auth.security import decode_access_token, session_matches_user
from config.settings import get_user_session, hydrate_user_session_from_db
from core.db.repository import DBRepo
from config.settings import DEFAULT_SESSION
logger = getLogger(__name__)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_auth(
    token: str = Depends(oauth2_scheme),
):
    """
    Extracts and validates JWT.
    Enforces auth session validity.
    This dependency is what enables Swagger auth.
    """

    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        session_id = payload.get("sid")

        if not user_id or not session_id:
            logger.warning("Invalid token - missing required claims")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token",
            )

    except JWTError as e:
        logger.warning(
            "JWT validation failed",
            extra={"error": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )

    async with AsyncSessionLocal() as db:
        session = await db.get(AuthSession, session_id)

        if not session_matches_user(session, user_id):
            # Includes "no such session": a token must be backed by a session
            # that belongs to the user it names (see session_matches_user).
            logger.warning(
                "Session missing or does not belong to the token's user",
                extra={"session_id": session_id, "user_id": user_id},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired or revoked",
            )

        if session.revoked:
            logger.warning(
                "Session invalid or revoked",
                extra={"session_id": session_id, "user_id": user_id},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired or revoked",
            )
        # Optional but recommended: enforce session expiry
        expires_at = session.expires_at

        if expires_at:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            if expires_at < datetime.now(timezone.utc):
                logger.warning(
                    "Session expired",
                    extra={"session_id": session_id, "user_id": user_id},
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Session expired",
                )
        # ------------------------------------------------------------
        # 3. Lazy runtime session hydration (RESTART-SAFE)
        # ------------------------------------------------------------
        runtime_session = get_user_session(user_id)

        REQUIRED_KEYS = ("provider", "model")

        needs_hydration = (
            runtime_session is None
            or any(k not in runtime_session or not runtime_session[k] for k in REQUIRED_KEYS)
        )

        if needs_hydration:
            prefs = await DBRepo.get_user_preferences(db, user_id)

            if prefs:
                # Single canonical mapping — see core/auth/hydration.py (4.1 fix)
                from core.auth.hydration import build_prefs_dict
                hydrate_user_session_from_db(user_id, build_prefs_dict(prefs))
            else:
                # No prefs stored yet; use defaults
                hydrate_user_session_from_db(user_id, DEFAULT_SESSION)

    logger.debug(
        "Authentication successful",
        extra={"user_id": user_id, "session_id": session_id},
    )
    return {
        "user_id": user_id,
        "session_id": session_id,
    }
