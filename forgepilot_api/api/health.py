from __future__ import annotations

from fastapi import APIRouter
import time

router = APIRouter(prefix="/health", tags=["health"])
_start_time = time.time()


@router.get("")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
        "uptime": time.time() - _start_time,
    }


@router.get("/dependencies")
async def dependencies() -> dict:
    return {
        "success": True,
        "allRequiredInstalled": True,
        "claudeCode": True,
        "dependencies": [],
    }


@router.get("/dependencies/{dep_id}")
async def dependency_detail(dep_id: str) -> dict:
    return {
        "success": True,
        "installed": True,
        "id": dep_id,
        "message": "No external CLI dependencies required. Agent runs in-process via forgepilot_sdk.",
    }


@router.get("/dependencies/{dep_id}/install-commands")
async def dependency_commands(dep_id: str) -> dict:
    return {
        "success": True,
        "id": dep_id,
        "commands": {},
        "message": "No installation needed. Agent runs in-process.",
    }


@router.post("/dependencies/{dep_id}/install")
async def install_dependency(dep_id: str) -> dict:
    return {
        "success": True,
        "installed": True,
        "id": dep_id,
        "message": "No installation needed. Agent runs in-process.",
    }

