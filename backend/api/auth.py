import asyncio
from fastapi import APIRouter, Depends, Request, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta, timezone
from fastapi.security import OAuth2PasswordRequestForm
from logging import getLogger

from core.db.session import AsyncSessionLocal
from api.deps import get_db
from core.db.repository import AuthRepo, DBRepo, MemoryRepo
from core.auth.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
)
from core.auth.dependencies import get_current_auth
from core.auth.schemas import (
    SignupRequest, TokenResponse, RefreshRequest,
    UserProfileResponse, UpdateProfileRequest, ChangePasswordRequest,
    SSETokenResponse, StatusResponse,
)
from core.db.models import User
from config.settings import (
    hydrate_user_session_from_db, get_user_session, DEFAULT_SESSION,
    AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS, AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS,
    AUTH_SIGNUP_RATE_LIMIT_WINDOW_SECONDS, AUTH_SIGNUP_RATE_LIMIT_MAX_CALLS,
    AUTH_REFRESH_RATE_LIMIT_WINDOW_SECONDS, AUTH_REFRESH_RATE_LIMIT_MAX_CALLS,
)
from core.hooks import HookRunner
from core.memory.fact_summary import schedule_summary_rebuild
from utils.workspace import get_user_workspace
from core.rate_limit import SlidingWindowRateLimiter

router = APIRouter(prefix="/auth", tags=["auth"])
logger = getLogger(__name__)


# ── Helper: seconds until access token expires ────────────────────────────────
_ACCESS_TOKEN_TTL_SECONDS: int = TOKEN_EXPIRE_MINUTES * 60


# ── S-02: rate limiters for the unauthenticated auth endpoints (POST
# /auth/{login,signup,refresh}) -- kept even though the packaged build now
# binds to 127.0.0.1 by default (B2-1.5, 2026-08-18), since EVA_HOST can
# still be overridden to expose the server wider; see config/settings.py's
# "Auth rate limiting" section for the full rationale. /chat and /execute
# are not limited here -- they already require a valid Bearer token.
_login_rate_limiter = SlidingWindowRateLimiter(
    window_seconds=AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS,
    max_calls=AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS,
    message="Too many failed login attempts. Please try again later.",
)
_signup_rate_limiter = SlidingWindowRateLimiter(
    window_seconds=AUTH_SIGNUP_RATE_LIMIT_WINDOW_SECONDS,
    max_calls=AUTH_SIGNUP_RATE_LIMIT_MAX_CALLS,
    message="Too many signup attempts from this address. Please try again later.",
)
_refresh_rate_limiter = SlidingWindowRateLimiter(
    window_seconds=AUTH_REFRESH_RATE_LIMIT_WINDOW_SECONDS,
    max_calls=AUTH_REFRESH_RATE_LIMIT_MAX_CALLS,
    message="Too many token refresh attempts from this address. Please try again later.",
)


def _client_ip(request: Request) -> str:
    """Best-effort client IP for rate-limit keying; a stable fallback
    bucket (not None/crash) when the client info is unavailable."""
    return request.client.host if request and request.client else "unknown"


# get_db lives in api/deps.py (imported above) — no local definition needed


# ── Internal helper: issue refresh token and persist it ───────────────────────

async def _issue_refresh_token(db: AsyncSession, session_id: str) -> str:
    """
    Generate a new refresh token, store it on the auth session, and return it.
    Called during signup, login, and token refresh (B-010).
    """
    token = create_refresh_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    await AuthRepo.set_refresh_token(db, session_id, token, expires_at)
    return token


# ---------------------------------------------------------------------
# SIGNUP
# ---------------------------------------------------------------------
@router.post("/signup", response_model=TokenResponse)
async def signup(
    data: SignupRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # S-02: anti-spam limiter on the unauthenticated signup endpoint.
    await _signup_rate_limiter.check_and_record(_client_ip(request))

    logger.info(
        "Signup attempt",
        extra={
            "username": data.username,
            "email": data.email,
            "ip_address": request.client.host if request and request.client else None,
        },
    )
    # Create user
    try:
        user = await AuthRepo.create_user(
            db,
            email=data.email,
            username=data.username,
            password_hash=hash_password(data.password),
        )
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="A user with this username or email already exists.",
        )
    logger.info(
        "User created",
        extra={"user_id": user.id, "username": data.username},
    )

    # Create auth session
    session = await AuthRepo.create_session(
        db=db,
        user_id=user.id,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request and request.client else None,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    logger.debug(
        "Auth session created",
        extra={"user_id": user.id, "session_id": session.id},
    )

    # Audit log
    await AuthRepo.log_event(
        db,
        user_id=user.id,
        session_id=session.id,
        event_type="signup",
        ip_address=request.client.host if request and request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    # Issue tokens (B-007: expires_in; B-010: refresh_token)
    access_token = create_access_token(user_id=user.id, session_id=session.id)
    refresh_token = await _issue_refresh_token(db, session.id)

    # V5.0 Sprint 5: Seed default agents and skills for the new user (B-074).
    # Non-fatal — failure never blocks signup.
    try:
        from core.defaults.seeder import seed_user_defaults  # noqa: PLC0415
        seed_result = await seed_user_defaults(user.id)
        logger.info(
            "Default library seeded for new user",
            extra={
                "user_id": user.id,
                "agents_seeded": seed_result["agents_seeded"],
                "skills_seeded": seed_result["skills_seeded"],
            },
        )
    except Exception as _seed_exc:
        logger.warning(
            "Default seeding failed (non-fatal): %s",
            _seed_exc,
            extra={"user_id": user.id},
        )

    logger.info(
        "Signup successful",
        extra={"user_id": user.id, "username": data.username},
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": _ACCESS_TOKEN_TTL_SECONDS,
        "refresh_token": refresh_token,
    }


# ---------------------------------------------------------------------
# LOGIN
# ---------------------------------------------------------------------
@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    # S-02: dual-key (IP + account) failure-counted brute-force limiter,
    # per OWASP's Authentication Cheat Sheet. Checked before touching the
    # DB or verifying the password -- a client already over the limit is
    # rejected outright, credentials not even considered this call.
    ip_key = f"ip:{_client_ip(request)}"
    user_key = f"user:{form_data.username}"
    await _login_rate_limiter.check(ip_key)
    await _login_rate_limiter.check(user_key)

    logger.info(
        "Login attempt",
        extra={
            "username": form_data.username,
            "ip_address": request.client.host if request and request.client else None,
        },
    )
    # Lookup user (by username or email – choose one)
    user = await AuthRepo.get_user_by_username(db, form_data.username)
    if not user or not verify_password(form_data.password, user.password_hash):
        # Only failed attempts count towards the limit -- a user who
        # eventually logs in correctly is never penalized for earlier typos.
        await _login_rate_limiter.record_failure(ip_key)
        await _login_rate_limiter.record_failure(user_key)
        logger.warning(
            "Failed login attempt",
            extra={
                "username": form_data.username,
                "ip_address": request.client.host if request and request.client else None,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # Successful login clears both counters.
    await _login_rate_limiter.reset(ip_key)
    await _login_rate_limiter.reset(user_key)

    logger.debug(
        "User authenticated",
        extra={"user_id": user.id, "username": form_data.username},
    )
    # Create auth session
    session = await AuthRepo.create_session(
        db=db,
        user_id=user.id,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request and request.client else None,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    logger.debug(
        "Auth session created",
        extra={"user_id": user.id, "session_id": session.id},
    )

    # Load ALL user preferences from DB — single canonical mapping (4.1 fix)
    from core.auth.hydration import build_prefs_dict
    prefs = await DBRepo.get_user_preferences(db, user.id)
    if prefs:
        logger.debug("Loading user preferences", extra={"user_id": user.id})
        hydrate_user_session_from_db(user.id, build_prefs_dict(prefs))
    else:
        # First login — use defaults
        logger.debug("First login - setting defaults", extra={"user_id": user.id})
        hydrate_user_session_from_db(user.id, DEFAULT_SESSION)

    # Fire SessionStart hooks — non-blocking, never delays login (B-064)
    _user_session = get_user_session(user.id)
    _hook_defs = _user_session.get("tool_hooks") or []
    if _hook_defs:
        asyncio.create_task(
            HookRunner.fire_event(
                event="session_start",
                hook_definitions=_hook_defs,
                user_id=user.id,
                session_id=session.id,
                workspace_root=get_user_workspace(user.id),
            ),
            name=f"hook-session-start-{user.id}",
        )

    # Audit log
    await AuthRepo.log_event(
        db,
        user_id=user.id,
        session_id=session.id,
        event_type="login",
        ip_address=request.client.host if request and request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    # Issue tokens (B-007: expires_in; B-010: refresh_token)
    access_token = create_access_token(user_id=user.id, session_id=session.id)
    refresh_token = await _issue_refresh_token(db, session.id)

    logger.info("Login successful", extra={"user_id": user.id, "username": form_data.username})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": _ACCESS_TOKEN_TTL_SECONDS,
        "refresh_token": refresh_token,
    }


# ---------------------------------------------------------------------
# REFRESH TOKEN  (B-010)
# ---------------------------------------------------------------------
@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    data: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Exchange a valid refresh token for a new access token.

    The refresh token is **rotated** on each use — the old token is
    immediately invalidated and a new one is returned.  This limits
    the exposure window if a refresh token is ever leaked.

    Refresh tokens expire after 7 days of inactivity.  After expiry
    the user must log in again.
    """
    # S-02: lightweight anti-hammering limiter (not brute-force protection
    # -- refresh tokens are high-entropy, not guessable). See
    # config/settings.py's "Auth rate limiting" section.
    await _refresh_rate_limiter.check_and_record(_client_ip(request))

    session = await AuthRepo.get_session_by_refresh_token(db, data.refresh_token)

    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    # Check refresh token expiry
    refresh_expires = session.refresh_expires_at
    if refresh_expires:
        if refresh_expires.tzinfo is None:
            refresh_expires = refresh_expires.replace(tzinfo=timezone.utc)
        if refresh_expires < datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token has expired — please log in again",
            )

    # Rotate refresh token (issue new, invalidate old)
    new_refresh_token = await _issue_refresh_token(db, session.id)

    # Issue new access token
    access_token = create_access_token(user_id=session.user_id, session_id=session.id)

    logger.info(
        "Token refreshed",
        extra={"user_id": session.user_id, "session_id": session.id},
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": _ACCESS_TOKEN_TTL_SECONDS,
        "refresh_token": new_refresh_token,
    }


# ---------------------------------------------------------------------
# SSE TOKEN  (B-008)
# ---------------------------------------------------------------------
@router.post("/sse-token", response_model=SSETokenResponse)
async def get_sse_token(auth=Depends(get_current_auth)):
    """
    Issue a short-lived (30s), single-use SSE token. (B-008)

    The browser EventSource API cannot send custom headers, so the
    JWT cannot be passed as a Bearer token for SSE connections.
    Embedding the full JWT in the URL query string exposes it in
    server access logs, browser history, and reverse-proxy logs.

    Solution: exchange the JWT (sent as a standard Bearer header here)
    for a short-lived SSE token.  The client then opens the SSE
    stream with ?sse_token=<uuid> instead of ?token=<jwt>.

    Token is stored in LocalBroker (in-memory, TTL=30s, single-use).
    """
    from core.broker import local_broker as _broker
    import uuid

    user_id = auth["user_id"]
    sse_token = str(uuid.uuid4())
    # Store user_id mapped to token; expire in 30 seconds; single-use
    await _broker.client.set(f"sse_token:{sse_token}", user_id, ex=30)

    logger.debug(
        "SSE token issued",
        extra={"user_id": user_id, "sse_token_prefix": sse_token[:8]},
    )
    return {
        "sse_token": sse_token,
        "expires_in": 30,
    }


# ---------------------------------------------------------------------
# LOGOUT
# ---------------------------------------------------------------------
@router.post("/logout", response_model=StatusResponse)
async def logout(
    auth=Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
):
    user_id = auth["user_id"]
    session_id = auth["session_id"]
    logger.info(
        "Logout requested",
        extra={"user_id": user_id, "session_id": session_id},
    )
    # Revoke session
    await AuthRepo.revoke_session(db, session_id)
    logger.debug(
        "Session revoked",
        extra={"user_id": user_id, "session_id": session_id},
    )

    # Audit log
    await AuthRepo.log_event(
        db,
        user_id=user_id,
        session_id=session_id,
        event_type="logout",
    )

    logger.info(
        "Logout successful",
        extra={"user_id": user_id},
    )
    return {"status": "logged out"}


# ---------------------------------------------------------------------
# GET /auth/me  (V6.0 — user profile)
# ---------------------------------------------------------------------
@router.get("/me", response_model=UserProfileResponse)
async def get_me(
    auth=Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the authenticated user's profile fields:
    user_id, username, email, full_name, description.
    """
    user = await AuthRepo.get_user_by_id(db, auth["user_id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserProfileResponse(
        user_id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        description=user.description,
    )


# ---------------------------------------------------------------------
# PATCH /auth/profile  (V6.0 — user profile)
# ---------------------------------------------------------------------
@router.patch("/profile", response_model=UserProfileResponse)
async def update_profile(
    data: UpdateProfileRequest,
    auth=Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Update the authenticated user's full_name and/or description.

    Memory side-effects (both are profile-locked):
      - full_name → upserts user.full_name fact (source="profile")
               → seeds user.name ONLY if slot is currently empty
      - description → upserts user.about fact (source="profile")

    Omit a field (or send null) to leave it unchanged.
    Send an empty string to clear the field.
    """
    user_id = auth["user_id"]
    fields_to_update = {}
    if data.full_name is not None:
        fields_to_update["full_name"] = data.full_name
    if data.description is not None:
        fields_to_update["description"] = data.description

    if not fields_to_update:
        # Nothing changed — just return current profile
        user = await AuthRepo.get_user_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return UserProfileResponse(
            user_id=user.id,
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            description=user.description,
        )

    user = await AuthRepo.update_user(db, user_id, **fields_to_update)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # ── Memory side-effects ──────────────────────────────────────────────
    # Use a fresh session so memory writes are independent of the user-row
    # session (following the pattern used by MetricsService.for_project).
    memory_wrote = False
    async with AsyncSessionLocal() as mem_db:
        if data.full_name is not None:
            if data.full_name:
                # Upsert user.full_name (always profile-locked)
                await MemoryRepo.upsert_profile_fact(
                    mem_db, user_id, "user.full_name", data.full_name
                )
            else:
                # full_name cleared — remove memory fact if present
                existing = await MemoryRepo.get_user_fact_by_slot(
                    mem_db, user_id, "user.full_name"
                )
                if existing:
                    await mem_db.delete(existing)
                    await mem_db.commit()
            memory_wrote = True

        if data.description is not None:
            if data.description:
                await MemoryRepo.upsert_profile_fact(
                    mem_db, user_id, "user.about", data.description
                )
            else:
                # description cleared — remove memory fact if present
                existing = await MemoryRepo.get_user_fact_by_slot(
                    mem_db, user_id, "user.about"
                )
                if existing:
                    await mem_db.delete(existing)
                    await mem_db.commit()
            memory_wrote = True

        # ── user.name (preferred_name) priority logic ────────────────────
        # Priority 1: explicit preferred_name provided → always overwrites user.name.
        #   Uses source="manual" (not "profile") so the LLM extractor can still
        #   evolve it freely from conversation (e.g. "call me Andy").
        # Priority 2: full_name provided + user.name slot currently empty →
        #   seed from first word of full_name (fallback, source="manual").
        # Priority 3: neither → leave user.name unchanged.
        if data.preferred_name is not None:
            if data.preferred_name:
                existing_name = await MemoryRepo.get_user_fact_by_slot(
                    mem_db, user_id, "user.name"
                )
                if existing_name:
                    await MemoryRepo.update_user_fact(
                        mem_db,
                        fact_id=existing_name.id,
                        user_id=user_id,
                        updates={"fact": data.preferred_name, "source": "manual"},
                    )
                else:
                    await MemoryRepo.add_user_fact(
                        mem_db,
                        user_id=user_id,
                        fact=data.preferred_name,
                        slot_key="user.name",
                        source="manual",
                        confidence=1.0,
                    )
                memory_wrote = True
            # preferred_name="" → clear user.name (send null/empty to unset)
            else:
                existing_name = await MemoryRepo.get_user_fact_by_slot(
                    mem_db, user_id, "user.name"
                )
                if existing_name:
                    await mem_db.delete(existing_name)
                    await mem_db.commit()
                memory_wrote = True
        elif data.full_name:
            # No explicit preferred_name — seed user.name from full_name only if empty
            existing_name = await MemoryRepo.get_user_fact_by_slot(
                mem_db, user_id, "user.name"
            )
            if not existing_name:
                casual_name = data.full_name.strip().split()[0]
                await MemoryRepo.add_user_fact(
                    mem_db,
                    user_id=user_id,
                    fact=casual_name,
                    slot_key="user.name",
                    source="manual",
                    confidence=1.0,
                )
                memory_wrote = True

    if memory_wrote:
        schedule_summary_rebuild(user_id)

    logger.info(
        "Profile updated",
        extra={"user_id": user_id, "fields": list(fields_to_update.keys())},
    )
    return UserProfileResponse(
        user_id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        description=user.description,
    )


# ---------------------------------------------------------------------
# POST /auth/change-password  (V6.0 — user profile)
# ---------------------------------------------------------------------
@router.post("/change-password", response_model=StatusResponse)
async def change_password(
    data: ChangePasswordRequest,
    auth=Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Change the authenticated user's password.

    Validates current_password before accepting new_password.
    Returns 400 if current_password is wrong.
    Returns 200 {"status": "password changed"} on success.
    """
    user_id = auth["user_id"]

    user = await AuthRepo.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    new_hash = hash_password(data.new_password)
    await AuthRepo.update_user(db, user_id, password_hash=new_hash)

    logger.info("Password changed", extra={"user_id": user_id})
    return {"status": "password changed"}
