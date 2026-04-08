from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from forgepilot_api.services.provider_service import (
    get_agent_providers,
    get_available_agent_providers,
    get_available_sandbox_providers,
    get_config,
    get_sandbox_providers,
    sync_settings,
    switch_agent_provider,
    switch_sandbox_provider,
)

router = APIRouter(prefix="/providers", tags=["providers"])

API_TIMEOUT_MS = 60000
DEFAULT_TEST_MODEL = "gpt-3.5-turbo"
DETECT_TEST_MESSAGE = "OK"


def _build_api_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if "/messages" in normalized:
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/messages"
    return f"{normalized}/v1/messages"


@router.get("/sandbox")
async def list_sandbox() -> dict:
    return await get_sandbox_providers()


@router.get("/sandbox/available")
async def sandbox_available() -> dict:
    return {"available": await get_available_sandbox_providers()}


@router.get("/sandbox/{provider_type}")
async def sandbox_detail(provider_type: str) -> dict:
    data = await get_sandbox_providers()
    for item in data["providers"]:
        if item["type"] == provider_type:
            return item
    return JSONResponse({"error": f"Sandbox provider not found: {provider_type}"}, status_code=404)


@router.post("/sandbox/switch")
async def sandbox_switch(body: dict) -> dict:
    provider_type = body.get("type")
    if not provider_type:
        return JSONResponse({"error": "Provider type is required"}, status_code=400)
    await switch_sandbox_provider(str(provider_type), body.get("config"))
    return {"success": True, "current": provider_type, "message": f"Switched to sandbox provider: {provider_type}"}


@router.get("/agents")
async def list_agents() -> dict:
    return await get_agent_providers()


@router.get("/agents/available")
async def agents_available() -> dict:
    return {"available": await get_available_agent_providers()}


@router.get("/agents/{provider_type}")
async def agent_detail(provider_type: str) -> dict:
    data = await get_agent_providers()
    for item in data["providers"]:
        if item["type"] == provider_type:
            return item
    return JSONResponse({"error": f"Agent provider not found: {provider_type}"}, status_code=404)


@router.post("/agents/switch")
async def agents_switch(body: dict) -> dict:
    provider_type = body.get("type")
    if not provider_type:
        return JSONResponse({"error": "Provider type is required"}, status_code=400)
    await switch_agent_provider(str(provider_type), body.get("config"))
    return {"success": True, "current": provider_type, "message": f"Switched to agent provider: {provider_type}"}


@router.post("/settings/sync")
async def settings_sync(body: dict) -> dict:
    config = await sync_settings(body)
    return {"success": True, "config": config}


@router.get("/config")
async def config() -> dict:
    return await get_config()


@router.post("/detect")
async def detect(body: dict[str, Any]) -> dict:
    base_url = body.get("baseUrl")
    api_key = body.get("apiKey")
    test_model = body.get("model") or DEFAULT_TEST_MODEL
    if not base_url or not api_key:
        return JSONResponse({"error": "baseUrl and apiKey are required"}, status_code=400)

    api_url = _build_api_url(str(base_url))
    timeout_s = API_TIMEOUT_MS / 1000
    payload = {
        "model": test_model,
        "messages": [{"role": "user", "content": DETECT_TEST_MESSAGE}],
        "max_tokens": 1,
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(
                api_url,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            if resp.is_success:
                return {
                    "success": True,
                    "message": "Connection successful! Configuration valid",
                    "model": test_model,
                    "response": resp.json(),
                }

        try:
            error_json = resp.json()
            error_text = error_json.get("error", {}).get("message") or f"HTTP {resp.status_code}"
        except Exception:
            error_text = f"HTTP {resp.status_code}"
        return {"success": False, "error": error_text}
    except (asyncio.TimeoutError, httpx.TimeoutException):
        return {"success": False, "error": "Connection timeout (60s)"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}

