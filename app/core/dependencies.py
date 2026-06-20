"""
app/core/dependencies.py

FastAPI dependency functions injected into route handlers via Depends().

Pattern:
  async def some_route(current_user = Depends(get_current_user)):
      ...

This keeps auth logic out of route handlers entirely.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.roles import UserRole
from app.core.security import decode_token
from app.db.postgres import get_db
from app.models.user import User
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Extract the Bearer token from the Authorization header,
    decode it, and return the User ORM object.

    Raises 401 if token is missing, invalid, expired, or the user
    no longer exists / is not verified.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not credentials:
        raise credentials_exception

    payload = decode_token(credentials.credentials)
    if payload is None or payload.get("type") != "access":
        raise credentials_exception

    user_id: str = payload.get("sub")
    if user_id is None:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception
    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email address not verified. Please check your inbox.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated.",
        )

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Alias — same as get_current_user, explicit name for readability."""
    return current_user


async def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Raises 403 if the user is not ADMIN or SUPER_ADMIN."""
    if current_user.role not in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return current_user


async def require_super_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Raises 403 if the user is not SUPER_ADMIN."""
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required.",
        )
    return current_user


def get_optional_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """
    Returns payload dict if a valid Bearer token is present, otherwise None.
    Used on public/guest endpoints where auth is optional (e.g. GET /p/{token}).
    Does NOT hit the database — callers that need the User object must query themselves.
    """
    if not credentials:
        return None
    return decode_token(credentials.credentials)
