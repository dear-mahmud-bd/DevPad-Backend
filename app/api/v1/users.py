"""
app/api/v1/users.py — User profile and cross-user note access
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.security import hash_password, verify_password
from app.db.mongodb import get_notes_collection
from app.db.postgres import get_db
from app.models.user import User
from app.schemas.user import ChangePasswordRequest, UserOut, UserUpdate
from app.services.kafka_producer import event_password_changed

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserOut)
async def get_profile(current_user: User = Depends(get_current_user)):
    """Return the authenticated user's profile."""
    return current_user


@router.put("/me", response_model=UserOut)
async def update_profile(
    body: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update username or email."""
    if body.username:
        current_user.username = body.username
    if body.email:
        current_user.email = body.email
        current_user.is_verified = False  # re-verify on email change
    return current_user


@router.put("/me/password", status_code=status.HTTP_200_OK)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Change password for the authenticated user.
    Requires the correct current password — prevents an attacker with a
    stolen access token from locking the real user out.
    """
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    if body.current_password == body.new_password:
        raise HTTPException(status_code=400, detail="New password must differ from the current password.")

    current_user.password_hash = hash_password(body.new_password)

    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")
    background_tasks.add_task(event_password_changed, current_user.id, ip, ua)

    return {"message": "Password changed successfully."}


@router.get("/{user_id}/notes")
async def get_user_notes(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Hybrid endpoint: verify user exists in PostgreSQL, then
    fetch their public/shared notes from MongoDB.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    notes_col = get_notes_collection()
    # Only return notes the requesting user has access to (or public ones)
    cursor = notes_col.find({
        "user_id": user_id,
        "is_deleted": False,
        "$or": [
            {"user_id": current_user.id},
            {"public_link_enabled": True},
        ]
    })
    docs = await cursor.to_list(length=50)
    for d in docs:
        d["id"] = str(d.pop("_id"))
    return docs
