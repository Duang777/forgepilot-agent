from __future__ import annotations

from fastapi import APIRouter, Query

from forgepilot_api.storage.repositories import list_audit_logs

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/logs")
async def get_audit_logs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    actor: str | None = Query(default=None),
    method: str | None = Query(default=None),
    path: str | None = Query(default=None),
    status_code: int | None = Query(default=None, ge=100, le=599),
) -> dict:
    data = await list_audit_logs(
        limit=limit,
        offset=offset,
        actor=actor,
        method=method,
        path=path,
        status_code=status_code,
    )
    return {
        "success": True,
        "items": data["items"],
        "total": data["total"],
        "limit": limit,
        "offset": offset,
    }
