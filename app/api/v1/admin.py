"""
Admin-only analytics and user management — powers the "Usage" tab of the web UI.

    GET   /admin/usage/summary?days=7        KPIs, daily series, per-model breakdown
    GET   /admin/usage/users?days=7          every user with requests / tokens / cost
    GET   /admin/usage/recent?limit=50       latest requests (metadata only, no message text)
    PATCH /admin/users/{username}/active     enable / disable an account
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.security import require_admin
from app.db import usage
from app.db.models import get_user_account, set_user_active
from app.utils.logger import logger

router = APIRouter(prefix="/admin", tags=["Admin"], dependencies=[Depends(require_admin)])

Days = Query(7, ge=0, le=365, description="Look-back window in days; 0 = all time")


class ActiveRequest(BaseModel):
    active: bool


@router.get("/usage/summary", summary="Usage KPIs, daily series and per-model totals")
def usage_summary(days: int = Days):
    return usage.usage_summary(days)


@router.get("/usage/users", summary="Per-user usage (requests, tokens, estimated cost)")
def usage_users(days: int = Days):
    return {"days": days, "users": usage.usage_by_user(days)}


@router.get("/usage/recent", summary="Most recent requests")
def usage_recent(limit: int = Query(50, ge=1, le=200), username: str | None = Query(None, max_length=100)):
    return {"requests": usage.usage_recent(limit, username.strip().lower() if username else None)}


@router.patch("/users/{username}/active", summary="Enable or disable a user account")
def set_active(username: str, body: ActiveRequest, admin: dict = Depends(require_admin)):
    username = username.strip().lower()
    if username == admin["username"]:
        raise HTTPException(status_code=400, detail="You cannot change your own account status")
    if not get_user_account(username):
        raise HTTPException(status_code=404, detail="User not found")
    set_user_active(username, body.active)
    logger.info("Account %s %s by %s", username, "enabled" if body.active else "DISABLED", admin["username"])
    return {"username": username, "is_active": body.active}
