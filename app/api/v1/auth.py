"""
app/api/v1/auth.py

Authentication endpoints:
  POST /auth/signup        → register, send verification email
  GET  /auth/verify-email  → activate account
  POST /auth/login         → return JWT access token
  POST /auth/refresh       → issue new access token from refresh cookie
  POST /auth/logout        → clear refresh cookie
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.roles import UserRole
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.postgres import get_db
from app.models.auth import EmailVerification
from app.models.user import User
from app.schemas.user import TokenOut, UserCreate, UserLogin, UserOut
from app.services import email as email_service
from app.services.kafka_producer import event_user_login, event_user_signup

settings = get_settings()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _get_client_info(request: Request) -> tuple[str, str]:
    """Extract IP and User-Agent from the request for activity logging."""
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")
    return ip, ua


# ── POST /auth/signup ────────────────────────────────────────────

@router.post("/signup", status_code=status.HTTP_201_CREATED, response_model=UserOut)
async def signup(
    body: UserCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Register a new user.
    1. Check email + username are not already taken.
    2. Hash the password.
    3. Assign role (SUPER_ADMIN if email is in the super_admin list).
    4. Create user (is_active=False, is_verified=False).
    5. Create a verification token.
    6. Send verification email in the background (non-blocking).
    7. Publish signup event to Kafka.
    """
    # Uniqueness checks
    existing = await db.execute(
        select(User).where(
            (User.email == body.email) | (User.username == body.username)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email or username already registered.",
        )

    # Determine role
    role = (
        UserRole.SUPER_ADMIN
        if body.email in settings.super_admin_email_list
        else UserRole.USER
    )

    user = User(
        email=body.email,
        username=body.username,
        password_hash=hash_password(body.password),
        role=role,
        is_active=False,
        is_verified=False,
    )
    db.add(user)
    await db.flush()   # assigns user.id without committing

    verification = EmailVerification(user_id=user.id)
    db.add(verification)
    await db.flush()

    token = verification.token

    # Send email in the background — route returns immediately
    background_tasks.add_task(
        email_service.send_verification_email,
        body.email,
        body.username,
        token,
    )

    ip, ua = _get_client_info(request)
    background_tasks.add_task(event_user_signup, user.id, body.email, ip, ua)

    logger.info("New user registered: %s (role=%s)", body.email, role)
    return user


# ── GET /auth/verify-email ───────────────────────────────────────

@router.get("/verify-email", status_code=status.HTTP_200_OK)
async def verify_email(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Activate the account tied to the verification token.
    Tokens are single-use and expire in 24 hours.
    """
    result = await db.execute(
        select(EmailVerification).where(EmailVerification.token == token)
    )
    record = result.scalar_one_or_none()

    if not record or record.used:
        raise HTTPException(status_code=400, detail="Invalid or already used verification link.")

    if datetime.now(timezone.utc) > record.expires_at:
        raise HTTPException(status_code=400, detail="Verification link has expired. Please sign up again.")

    # Activate user
    user_result = await db.execute(select(User).where(User.id == record.user_id))
    user = user_result.scalar_one()
    user.is_verified = True
    user.is_active = True

    record.used = True   # mark token consumed

    return {"message": "Email verified successfully. You can now log in."}


# ── POST /auth/login ─────────────────────────────────────────────

@router.post("/login", response_model=TokenOut)
async def login(
    body: UserLogin,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Login with username (or email) + password.
    Returns an access token in the body and sets a refresh token httpOnly cookie.
    """
    # Accept username or email in the 'username' field
    result = await db.execute(
        select(User).where(
            (User.username == body.username) | (User.email == body.username)
        )
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
        )

    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email address before logging in.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated.",
        )

    access_token = create_access_token(user.id, user.role)
    refresh_token = create_refresh_token(user.id)

    ip, ua = _get_client_info(request)
    background_tasks.add_task(event_user_login, user.id, ip, ua)

    response = JSONResponse(content={"access_token": access_token, "token_type": "bearer"})
    # httpOnly = JavaScript cannot read this cookie → protects against XSS
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=not settings.is_development,
        samesite="lax",
        max_age=settings.refresh_token_expire_days * 86400,
    )
    return response


# ── POST /auth/refresh ───────────────────────────────────────────

@router.post("/refresh", response_model=TokenOut)
async def refresh(
    refresh_token: str = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
):
    """
    Issue a new access token using the refresh token from the httpOnly cookie.
    The client calls this when it gets a 401 on any other endpoint.
    """
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Refresh token missing.")

    payload = decode_token(refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token.")

    user_id = int(payload["sub"])
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive.")

    new_access_token = create_access_token(user.id, user.role)
    return {"access_token": new_access_token, "token_type": "bearer"}


# ── POST /auth/logout ────────────────────────────────────────────

@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout():
    """Clear the refresh token cookie."""
    response = JSONResponse(content={"message": "Logged out successfully."})
    response.delete_cookie("refresh_token")
    return response
