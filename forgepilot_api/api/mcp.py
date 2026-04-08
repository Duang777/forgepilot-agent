from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from forgepilot_api.config import get_all_mcp_config_paths, get_primary_mcp_config_path

router = APIRouter(prefix="/mcp", tags=["mcp"])


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


@router.get("/config")
async def get_config() -> dict:
    config_path = get_primary_mcp_config_path()
    data = _read_json(config_path)
    return {
        "success": True,
        "data": {"mcpServers": data.get("mcpServers", data if isinstance(data, dict) else {})},
        "path": str(config_path),
    }


@router.post("/config")
async def set_config(body: dict) -> dict:
    if not isinstance(body.get("mcpServers"), dict):
        return JSONResponse(
            {"success": False, "error": "Invalid config format: mcpServers object required"},
            status_code=400,
        )
    path = get_primary_mcp_config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"success": True, "message": "MCP config saved", "path": str(path)}
    except Exception:
        return JSONResponse({"success": False, "error": "Failed to write MCP config"}, status_code=500)


@router.get("/path")
async def get_path() -> dict:
    return {"success": True, "path": str(get_primary_mcp_config_path())}


@router.get("/all-configs")
async def all_configs() -> dict:
    configs = []
    for item in get_all_mcp_config_paths():
        p = Path(item["path"])
        if p.exists():
            data = _read_json(p)
            configs.append(
                {
                    "name": item["name"],
                    "path": str(p),
                    "exists": True,
                    "servers": data.get("mcpServers", data if isinstance(data, dict) else {}),
                }
            )
        else:
            configs.append({"name": item["name"], "path": str(p), "exists": False, "servers": {}})
    return {"success": True, "configs": configs}


# Compatibility helper routes from earlier scaffold.
@router.get("")
async def list_mcp_servers() -> dict:
    cfg = await get_config()
    return {"mcpServers": cfg["data"]["mcpServers"]}


@router.post("/load")
async def load_from_path(body: dict) -> dict:
    path = body.get("path")
    if not path:
        return await list_mcp_servers()
    data = _read_json(Path(path))
    return {"mcpServers": data.get("mcpServers", data if isinstance(data, dict) else {})}

