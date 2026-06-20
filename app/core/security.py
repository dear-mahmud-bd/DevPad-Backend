"""
app/core/security.py

All cryptographic operations live here:
  - Password hashing (bcrypt via passlib)
  - JWT creation and verification (access + refresh tokens)

Nothing else in the app should touch passlib or jose directly.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()

# bcrypt is the industry standard for password hashing.
# deprecated="auto" means old schemes are flagged but still verifiable.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── Password helpers ────────────────────────────────────────────

def hash_password(plain: str) -> str:
    """Return bcrypt hash of a plain-text password."""
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if plain matches hashed. Constant-time comparison."""
    return pwd_context.verify(plain, hashed)


# ── JWT helpers ─────────────────────────────────────────────────

def _create_token(data: dict, expires_delta: timedelta) -> str:
    """
    Internal helper. Stamps 'exp' and 'iat' onto the payload and signs it.
    All times are UTC — never use naive datetimes with JWTs.
    """
    now = datetime.now(timezone.utc)
    payload = data.copy()
    payload.update({
        "iat": now,
        "exp": now + expires_delta,
    })
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_access_token(user_id: int, role: str) -> str:
    """
    Short-lived token (default 30 min) sent in every API response.
    Payload: sub (user_id as str), role, type=access
    """
    return _create_token(
        data={"sub": str(user_id), "role": role, "type": "access"},
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(user_id: int) -> str:
    """
    Long-lived token (default 7 days) used only to issue new access tokens.
    Payload: sub, type=refresh
    Stored in an httpOnly cookie (handled in the auth router).
    """
    return _create_token(
        data={"sub": str(user_id), "type": "refresh"},
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
    )


def decode_token(token: str) -> Optional[dict]:
    """
    Decode and verify a JWT. Returns the payload dict or None if invalid/expired.
    Callers should check the 'type' field to distinguish access vs refresh tokens.
    """
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
        )
        return payload
    except JWTError:
        return None
