"""
app/api/v1/admin.py

Super admin and admin endpoints:
  GET  /admin/activity      — view all system activity logs
  GET  /admin/health        — full infra health check
  POST /admin/crash-test    — intentionally kill this instance (super admin only)

These endpoints are not shown in public docs.
"""
import logging
import os

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import require_admin, require_super_admin
from app.db.mongodb import get_activity_logs_collection
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"], include_in_schema=False)


@router.get("/activity")
async def all_activity(
    limit: int = Query(default=50, le=200),
    current_user: User = Depends(require_admin),
):
    """
    Admin: view recent activity logs across all users.
    Sorted by timestamp descending.
    """
    col = get_activity_logs_collection()
    cursor = col.find({}).sort("timestamp", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    for d in docs:
        d["id"] = str(d.pop("_id"))
    return docs


@router.get("/health")
async def system_health(current_user: User = Depends(require_admin)):
    """
    Returns basic health info for the current instance.
    Future: add Redis ping, MongoDB ping, Kafka broker count.
    """
    return {
        "status": "ok",
        "instance": os.getenv("INSTANCE_ID", "unknown"),
        "pid": os.getpid(),
    }


@router.post("/crash-test")
async def crash_test(current_user: User = Depends(require_super_admin)):
    """
    Super admin only. Intentionally kills this API instance with SIGKILL.
    Used to verify Nginx fails over to the other instance correctly.
    WARNING: this terminates the process immediately.
    """
    logger.warning(
        "CRASH TEST triggered by super_admin user_id=%s — process will exit now.",
        current_user.id,
    )
    # Publish infra event before dying (best effort)
    from app.services.kafka_producer import event_infra
    await event_infra("infra_api_crash", {"triggered_by": current_user.id})
    os.kill(os.getpid(), 9)   # SIGKILL — immediate, no cleanup
