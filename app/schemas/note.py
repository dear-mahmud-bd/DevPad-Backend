"""
app/schemas/note.py

Pydantic schemas for note API requests and responses.
Note documents live in MongoDB — there is no SQLAlchemy model for notes.
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Create / Update ─────────────────────────────────────────────

class NoteCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(default="")
    tags: List[str] = Field(default_factory=list)


class NoteUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=300)
    content: Optional[str] = None
    tags: Optional[List[str]] = None


# ── Response ────────────────────────────────────────────────────

class NoteOut(BaseModel):
    """Full note as returned to the owner or an authorised user."""
    id: str                     # MongoDB ObjectId as string
    user_id: int                # owner's PostgreSQL user id
    title: str
    content: str
    tags: List[str]
    public_link_enabled: bool
    public_token: Optional[str]
    is_deleted: bool
    deleted_at: Optional[datetime]
    auto_delete_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class NotePublic(BaseModel):
    """Note visible to a guest via public link — no owner metadata."""
    id: str
    title: str
    content: str
    tags: List[str]
    created_at: datetime
    updated_at: datetime


# ── Sharing / permissions ────────────────────────────────────────

class ShareNoteRequest(BaseModel):
    invitee_email: str
    permission: str = Field(pattern="^(view|edit)$")


class PublicLinkOut(BaseModel):
    public_token: str
    public_url: str
