"""
app/api/v1/trash.py — Trash management
"""
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from app.core.dependencies import get_current_user
from app.db.mongodb import get_notes_collection
from app.models.user import User
from app.services.kafka_producer import event_note_restored

router = APIRouter(prefix="/trash", tags=["trash"])


@router.get("")
async def list_trash(current_user: User = Depends(get_current_user)):
    """List notes currently in the trash for the authenticated user."""
    notes_col = get_notes_collection()
    cursor = notes_col.find({"user_id": current_user.id, "is_deleted": True})
    docs = await cursor.to_list(length=100)
    for d in docs:
        d["id"] = str(d.pop("_id"))
    return docs


@router.post("/{note_id}/restore", status_code=status.HTTP_200_OK)
async def restore_note(
    note_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """Restore a trashed note."""
    notes_col = get_notes_collection()
    doc = await notes_col.find_one({"_id": ObjectId(note_id), "is_deleted": True})
    if not doc or doc["user_id"] != current_user.id:
        raise HTTPException(status_code=404, detail="Trashed note not found.")

    await notes_col.update_one(
        {"_id": ObjectId(note_id)},
        {"$set": {"is_deleted": False, "deleted_at": None, "auto_delete_at": None}},
    )
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")
    background_tasks.add_task(event_note_restored, current_user.id, note_id, ip, ua)
    return {"message": "Note restored."}


@router.delete("/{note_id}", status_code=status.HTTP_200_OK)
async def permanent_delete(
    note_id: str,
    current_user: User = Depends(get_current_user),
):
    """Permanently delete a note from trash immediately."""
    notes_col = get_notes_collection()
    doc = await notes_col.find_one({"_id": ObjectId(note_id), "is_deleted": True})
    if not doc or doc["user_id"] != current_user.id:
        raise HTTPException(status_code=404, detail="Trashed note not found.")
    await notes_col.delete_one({"_id": ObjectId(note_id)})
    return {"message": "Note permanently deleted."}
