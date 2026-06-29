"""
app/models/auth.py

SQLAlchemy ORM models for:
  - EmailVerification    (one-time tokens for email confirmation)
  - PasswordResetToken   (one-time tokens for password reset)
  - NotePermissionModel  (who can view/edit a shared note)
  - CollaborationInvite  (pending email invitations)

All stored in PostgreSQL because they are relational data that
requires joins, foreign-key constraints, and transactional safety.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.roles import InviteStatus, NotePermission
from app.db.postgres import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _1h_from_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=1)


def _24h_from_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=24)


def _72h_from_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=72)


def _new_uuid() -> str:
    return str(uuid.uuid4())


# ── Email Verification ──────────────────────────────────────────

class EmailVerification(Base):
    """
    One row per verification email sent.
    On successful verification: mark used=True and set user.is_verified=True.
    Expired rows are cleaned up by a periodic task (or on next signup attempt).
    """
    __tablename__ = "email_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, default=_new_uuid
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_24h_from_now
    )
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="verifications")  # noqa: F821


# ── Password Reset ──────────────────────────────────────────────

class PasswordResetToken(Base):
    """
    One row per password-reset request.
    Token is single-use and expires in 1 hour.
    Old unused tokens for the same user are invalidated on new request.
    """
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, default=_new_uuid
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_1h_from_now
    )
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="password_reset_tokens")  # noqa: F821


# ── Note Permissions ────────────────────────────────────────────

class NotePermissionModel(Base):
    """
    Tracks which registered user has what level of access to a specific note.
    note_id is a MongoDB ObjectId string (no FK — crosses DB boundary).
    """
    __tablename__ = "note_permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    note_id: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    granted_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    permission: Mapped[NotePermission] = mapped_column(
        Enum(NotePermission, name="note_permission_enum", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(  # noqa: F821
        "User", foreign_keys=[user_id], back_populates="note_permissions"
    )


# ── Collaboration Invites ───────────────────────────────────────

class CollaborationInvite(Base):
    """
    A pending invitation sent via email.
    When the invitee clicks the link, they are added to note_permissions
    and this row is marked accepted.
    Expired rows (72 hours) are ignored on acceptance attempt.
    """
    __tablename__ = "collaboration_invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    note_id: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    inviter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    invitee_email: Mapped[str] = mapped_column(String(255), nullable=False)
    permission: Mapped[NotePermission] = mapped_column(
        Enum(NotePermission, name="note_permission_enum", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    token: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, default=_new_uuid
    )
    status: Mapped[InviteStatus] = mapped_column(
        Enum(InviteStatus, name="invite_status_enum", values_callable=lambda x: [e.value for e in x]),
        default=InviteStatus.PENDING,
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_72h_from_now
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
