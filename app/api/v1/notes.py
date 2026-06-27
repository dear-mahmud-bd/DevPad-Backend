"""
app/api/v1/notes.py

Note endpoints (Phase 1 core — search/sharing in later phases):
  POST   /notes                         create note
  GET    /notes                         list user's notes
  GET    /notes/{id}                    get single note (owner, collaborator, or public)
  PUT    /notes/{id}                    update note
  DELETE /notes/{id}                    soft-delete → trash
  POST   /notes/{id}/share              invite collaborator via email
  GET    /notes/accept-invite           accept collaboration invite
  GET    /notes/{id}/permissions        list who has access
  DELETE /notes/{id}/permissions/{uid}  revoke access
  POST   /notes/{id}/public-link        enable public link
  DELETE /notes/{id}/public-link        disable public link
  GET    /p/{token}                     view note via public link (guest)
"""
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import get_current_user, get_optional_user
from app.core.roles import InviteStatus, NotePermission
from app.db.mongodb import get_notes_collection
from app.db.postgres import get_db
from app.models.auth import CollaborationInvite, NotePermissionModel
from app.models.user import User
from app.schemas.note import NoteCreate, NoteOut, NotePublic, NoteUpdate, ShareNoteRequest
from app.services import email as email_service
from app.services.kafka_producer import (
    event_guest_visit,
    event_invite_accepted,
    event_invite_sent,
    event_note_created,
    event_note_trashed,
    event_note_updated,
    event_note_viewed,
)

settings = get_settings()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notes", tags=["notes"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ip_ua(request: Request) -> tuple[str, str]:
    return (request.client.host if request.client else ""), request.headers.get("user-agent", "")


def _doc_to_out(doc: dict) -> dict:
    """Convert MongoDB document to NoteOut-compatible dict."""
    doc["id"] = str(doc.pop("_id"))
    return doc


# ── POST /notes ──────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED, response_model=NoteOut)
async def create_note(
    body: NoteCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    notes_col = get_notes_collection()
    now = _now()
    doc = {
        "user_id": current_user.id,
        "title": body.title,
        "content": body.content,
        "tags": body.tags,
        "public_link_enabled": False,
        "public_token": None,
        "is_deleted": False,
        "deleted_at": None,
        "auto_delete_at": None,
        "created_at": now,
        "updated_at": now,
    }
    result = await notes_col.insert_one(doc)
    doc["_id"] = result.inserted_id

    ip, ua = _ip_ua(request)
    background_tasks.add_task(
        event_note_created, current_user.id, str(result.inserted_id), body.title, ip, ua
    )
    return _doc_to_out(doc)


# ── GET /notes ───────────────────────────────────────────────────

@router.get("", response_model=list[NoteOut])
async def list_notes(
    current_user: User = Depends(get_current_user),
):
    notes_col = get_notes_collection()
    cursor = notes_col.find({"user_id": current_user.id, "is_deleted": False})
    docs = await cursor.to_list(length=100)
    return [_doc_to_out(d) for d in docs]


# ── GET /notes/accept-invite ──────────────────────────────────────
# Must be defined BEFORE /{note_id} so FastAPI does not swallow it as a note_id.

@router.get("/accept-invite", status_code=status.HTTP_200_OK)
async def accept_invite(
    token: str,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CollaborationInvite).where(CollaborationInvite.token == token)
    )
    invite = result.scalar_one_or_none()
    if not invite or invite.status != InviteStatus.PENDING:
        raise HTTPException(status_code=400, detail="Invalid or already used invite.")
    if _now() > invite.expires_at:
        invite.status = InviteStatus.EXPIRED
        raise HTTPException(status_code=400, detail="Invite has expired.")
    if current_user.email != invite.invitee_email:
        raise HTTPException(status_code=403, detail="This invite was sent to a different email.")

    perm = NotePermissionModel(
        note_id=invite.note_id,
        user_id=current_user.id,
        granted_by=invite.inviter_id,
        permission=invite.permission,
    )
    db.add(perm)
    invite.status = InviteStatus.ACCEPTED

    ip, ua = _ip_ua(request)
    background_tasks.add_task(event_invite_accepted, current_user.id, invite.note_id, ip, ua)
    return {"message": "Invite accepted. You now have access to the note."}


# ── GET /notes/{id} ──────────────────────────────────────────────

@router.get("/{note_id}", response_model=NoteOut)
async def get_note(
    note_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    notes_col = get_notes_collection()
    doc = await notes_col.find_one({"_id": ObjectId(note_id), "is_deleted": False})
    if not doc:
        raise HTTPException(status_code=404, detail="Note not found.")

    # Access: owner OR has a permission record
    if doc["user_id"] != current_user.id:
        perm = await db.execute(
            select(NotePermissionModel).where(
                NotePermissionModel.note_id == note_id,
                NotePermissionModel.user_id == current_user.id,
            )
        )
        if not perm.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="Access denied.")

    ip, ua = _ip_ua(request)
    background_tasks.add_task(event_note_viewed, current_user.id, None, note_id, ip, ua)
    return _doc_to_out(doc)


# ── PUT /notes/{id} ──────────────────────────────────────────────

@router.put("/{note_id}", response_model=NoteOut)
async def update_note(
    note_id: str,
    body: NoteUpdate,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    notes_col = get_notes_collection()
    doc = await notes_col.find_one({"_id": ObjectId(note_id), "is_deleted": False})
    if not doc:
        raise HTTPException(status_code=404, detail="Note not found.")

    # Owner OR user with edit permission
    if doc["user_id"] != current_user.id:
        perm = await db.execute(
            select(NotePermissionModel).where(
                NotePermissionModel.note_id == note_id,
                NotePermissionModel.user_id == current_user.id,
                NotePermissionModel.permission == NotePermission.EDIT,
            )
        )
        if not perm.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="Edit access required.")

    updates = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    updates["updated_at"] = _now()
    await notes_col.update_one({"_id": ObjectId(note_id)}, {"$set": updates})
    updated = await notes_col.find_one({"_id": ObjectId(note_id)})

    ip, ua = _ip_ua(request)
    background_tasks.add_task(event_note_updated, current_user.id, note_id, ip, ua)
    return _doc_to_out(updated)


# ── DELETE /notes/{id} (soft delete → trash) ─────────────────────

@router.delete("/{note_id}", status_code=status.HTTP_200_OK)
async def delete_note(
    note_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    notes_col = get_notes_collection()
    doc = await notes_col.find_one({"_id": ObjectId(note_id), "is_deleted": False})
    if not doc:
        raise HTTPException(status_code=404, detail="Note not found.")
    if doc["user_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Only the owner can delete a note.")

    now = _now()
    auto_delete = now + timedelta(days=settings.trash_auto_delete_days)
    await notes_col.update_one(
        {"_id": ObjectId(note_id)},
        {"$set": {"is_deleted": True, "deleted_at": now, "auto_delete_at": auto_delete}},
    )

    ip, ua = _ip_ua(request)
    background_tasks.add_task(event_note_trashed, current_user.id, note_id, ip, ua)
    return {"message": f"Note moved to trash. Auto-deleted in {settings.trash_auto_delete_days} days."}


# ── POST /notes/{id}/share ────────────────────────────────────────

@router.post("/{note_id}/share", status_code=status.HTTP_201_CREATED)
async def share_note(
    note_id: str,
    body: ShareNoteRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    notes_col = get_notes_collection()
    doc = await notes_col.find_one({"_id": ObjectId(note_id), "is_deleted": False})
    if not doc:
        raise HTTPException(status_code=404, detail="Note not found.")
    if doc["user_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Only the owner can share a note.")

    invite = CollaborationInvite(
        note_id=note_id,
        inviter_id=current_user.id,
        invitee_email=body.invitee_email,
        permission=NotePermission(body.permission),
    )
    db.add(invite)
    await db.flush()

    background_tasks.add_task(
        email_service.send_collaboration_invite,
        body.invitee_email,
        current_user.username,
        doc["title"],
        body.permission,
        invite.token,
    )
    ip, ua = _ip_ua(request)
    background_tasks.add_task(
        event_invite_sent, current_user.id, note_id, body.invitee_email, body.permission, ip, ua
    )
    return {"message": f"Invitation sent to {body.invitee_email}."}


# ── GET /notes/{id}/permissions ───────────────────────────────────

@router.get("/{note_id}/permissions")
async def list_permissions(
    note_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    notes_col = get_notes_collection()
    doc = await notes_col.find_one({"_id": ObjectId(note_id)})
    if not doc or doc["user_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Only the note owner can view permissions.")

    result = await db.execute(
        select(NotePermissionModel).where(NotePermissionModel.note_id == note_id)
    )
    perms = result.scalars().all()
    return [{"user_id": p.user_id, "permission": p.permission, "granted_at": p.granted_at} for p in perms]


# ── DELETE /notes/{id}/permissions/{uid} ─────────────────────────

@router.delete("/{note_id}/permissions/{user_id}", status_code=status.HTTP_200_OK)
async def revoke_permission(
    note_id: str,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    notes_col = get_notes_collection()
    doc = await notes_col.find_one({"_id": ObjectId(note_id)})
    if not doc or doc["user_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Only the note owner can revoke access.")

    result = await db.execute(
        select(NotePermissionModel).where(
            NotePermissionModel.note_id == note_id,
            NotePermissionModel.user_id == user_id,
        )
    )
    perm = result.scalar_one_or_none()
    if not perm:
        raise HTTPException(status_code=404, detail="Permission record not found.")
    await db.delete(perm)
    return {"message": "Access revoked."}


# ── POST /notes/{id}/public-link ──────────────────────────────────

@router.post("/{note_id}/public-link")
async def enable_public_link(
    note_id: str,
    current_user: User = Depends(get_current_user),
):
    notes_col = get_notes_collection()
    doc = await notes_col.find_one({"_id": ObjectId(note_id)})
    if not doc or doc["user_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Only the note owner can create a public link.")

    token = secrets.token_urlsafe(16)
    await notes_col.update_one(
        {"_id": ObjectId(note_id)},
        {"$set": {"public_link_enabled": True, "public_token": token}},
    )
    return {"public_token": token, "public_url": f"/p/{token}"}


# ── DELETE /notes/{id}/public-link ────────────────────────────────

@router.delete("/{note_id}/public-link")
async def disable_public_link(
    note_id: str,
    current_user: User = Depends(get_current_user),
):
    notes_col = get_notes_collection()
    doc = await notes_col.find_one({"_id": ObjectId(note_id)})
    if not doc or doc["user_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied.")
    await notes_col.update_one(
        {"_id": ObjectId(note_id)},
        {"$set": {"public_link_enabled": False, "public_token": None}},
    )
    return {"message": "Public link disabled."}


# ── GET /p/{token} — guest public view ───────────────────────────
# Note: this is mounted outside the /notes prefix in main.py

public_router = APIRouter(tags=["public"])


@public_router.get("/p/{token}", response_model=NotePublic)
async def view_public_note(
    token: str,
    request: Request,
    background_tasks: BackgroundTasks,
    maybe_user: dict = Depends(get_optional_user),
):
    notes_col = get_notes_collection()
    doc = await notes_col.find_one(
        {"public_token": token, "public_link_enabled": True, "is_deleted": False}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Note not found or link has been disabled.")

    # Log guest visit (user_id if logged in, random guest_id otherwise)
    guest_id = str(uuid.uuid4()) if not maybe_user else None
    user_id = int(maybe_user["sub"]) if maybe_user else None
    ip, ua = _ip_ua(request)

    background_tasks.add_task(
        event_note_viewed, user_id, guest_id, str(doc["_id"]), ip, ua
    )
    if guest_id:
        background_tasks.add_task(event_guest_visit, guest_id, f"/p/{token}", ip, ua)

    return _doc_to_out(doc)
