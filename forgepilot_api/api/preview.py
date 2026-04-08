from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from forgepilot_api.services.preview_service import (
    get_status,
    is_node_available,
    start_preview,
    stop_all,
    stop_preview,
)

router = APIRouter(prefix="/preview", tags=["preview"])


@router.get("/node-available")
async def node_available() -> dict[str, bool]:
    return {"available": is_node_available()}


@router.post("/start")
async def start(body: dict) -> dict:
    try:
        task_id = body.get("taskId")
        work_dir = body.get("workDir")
        port = body.get("port")
        if not task_id:
            return JSONResponse({"error": "taskId is required"}, status_code=400)
        if not work_dir:
            return JSONResponse({"error": "workDir is required"}, status_code=400)
        return await start_preview(str(task_id), str(work_dir), int(port) if port else None)
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@router.post("/stop")
async def stop(body: dict) -> dict:
    try:
        task_id = body.get("taskId")
        if not task_id:
            return JSONResponse({"error": "taskId is required"}, status_code=400)
        return await stop_preview(str(task_id))
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@router.get("/status/{task_id}")
async def status(task_id: str) -> dict:
    try:
        if not task_id:
            return JSONResponse({"error": "taskId is required"}, status_code=400)
        return get_status(task_id)
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@router.post("/stop-all")
async def stop_all_route() -> dict:
    try:
        await stop_all()
        return {"success": True, "message": "All preview servers stopped"}
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

