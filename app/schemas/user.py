"""
app/schemas/user.py

Pydantic schemas define the shape of API request bodies and responses.

Rule: ORM models (app/models/) are never returned directly from routes.
      Always convert to a schema first. This prevents accidentally
      leaking fields like password_hash.

Naming convention:
  - *Create  → request body for creating a resource
  - *Update  → request body for partial updates
  - *Out     → response body sent to the client
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.roles import UserRole


# ── Signup ──────────────────────────────────────────────────────

class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=30)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def username_alphanumeric(cls, v: str) -> str:
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Username may only contain letters, numbers, hyphens, and underscores.")
        return v.lower()


# ── Login ───────────────────────────────────────────────────────

class UserLogin(BaseModel):
    username: str       # accept username OR email (service layer decides)
    password: str


# ── Token response ──────────────────────────────────────────────

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    # refresh token is set as an httpOnly cookie, not returned in body


# ── User responses ──────────────────────────────────────────────

class UserOut(BaseModel):
    """Safe user object — never includes password_hash."""
    id: int
    email: EmailStr
    username: str
    role: UserRole
    is_active: bool
    is_verified: bool
    created_at: datetime

    model_config = {"from_attributes": True}   # allows ORM → schema conversion


class UserPublic(BaseModel):
    """Minimal user info for public display (e.g. note author)."""
    id: int
    username: str

    model_config = {"from_attributes": True}


# ── Profile update ───────────────────────────────────────────────

class UserUpdate(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=30)
    email: Optional[EmailStr] = None


# ── Password change (authenticated) ─────────────────────────────

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


# ── Password reset ───────────────────────────────────────────────

class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


# ── Re-verification ──────────────────────────────────────────────

class ResendVerificationRequest(BaseModel):
    email: EmailStr
