from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

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
DETECT_TEST_MESSAGE = "OK"
AuthType = Literal["bearer", "anthropic-key"]


@dataclass(frozen=True)
class DetectProviderSpec:
    endpoint_path: str
    default_model: str
    auth_type: AuthType
    default_api_prefix: str = ""


DETECT_PROVIDER_SPECS: dict[str, DetectProviderSpec] = {
    "openai-completions": DetectProviderSpec(
        endpoint_path="/chat/completions",
        default_model="gpt-3.5-turbo",
        auth_type="bearer",
        default_api_prefix="/v1",
    ),
    "anthropic-messages": DetectProviderSpec(
        endpoint_path="/messages",
        default_model="claude-3-5-haiku-latest",
        auth_type="anthropic-key",
        default_api_prefix="/v1",
    ),
}


def _looks_like_origin(base_url: str) -> bool:
    parsed = urlparse(base_url)
    return parsed.path.rstrip("/") == ""


def _build_api_url(base_url: str, spec: DetectProviderSpec) -> str:
    normalized = base_url.rstrip("/")

    if normalized.endswith(spec.endpoint_path):
        return normalized

    if spec.default_api_prefix and _looks_like_origin(normalized):
        normalized = f"{normalized}{spec.default_api_prefix}"

    return f"{normalized}{spec.endpoint_path}"


def _build_headers(spec: DetectProviderSpec, api_key: str) -> dict[str, str]:
    if spec.auth_type == "bearer":
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

    if spec.auth_type == "anthropic-key":
        return {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }

    raise ValueError(f"Unsupported auth type: {spec.auth_type}")


def _build_detect_payload(model: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": DETECT_TEST_MESSAGE}],
        "max_tokens": 1,
        "stream": False,
    }


def _extract_error_message(resp: httpx.Response) -> str:
    try:
        error_json = resp.json()
    except Exception:
        return f"HTTP {resp.status_code}"

    if isinstance(error_json, dict):
        error = error_json.get("error")

        if isinstance(error, dict):
            return str(error.get("message") or f"HTTP {resp.status_code}")

        if isinstance(error, str):
            return error

        if error_json.get("message"):
            return str(error_json["message"])

    return f"HTTP {resp.status_code}"


def _safe_response_body(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return resp.text


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
    api_type = str(body.get("apiType") or "openai-completions")

    spec = DETECT_PROVIDER_SPECS.get(api_type)
    if spec is None:
        return JSONResponse(
            {
                "error": (
                    "apiType must be one of: "
                    f"{', '.join(DETECT_PROVIDER_SPECS.keys())}"
                )
            },
            status_code=400,
        )

    if not base_url or not api_key:
        return JSONResponse({"error": "baseUrl and apiKey are required"}, status_code=400)

    test_model = str(body.get("model") or spec.default_model)
    api_url = _build_api_url(str(base_url), spec)
    timeout_s = API_TIMEOUT_MS / 1000
    payload = _build_detect_payload(test_model)
    headers = _build_headers(spec, str(api_key))

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(
                api_url,
                headers=headers,
                json=payload,
            )

        if resp.is_success:
            return {
                "success": True,
                "message": "Connection successful! Configuration valid",
                "apiType": api_type,
                "model": test_model,
                "url": api_url,
                "response": _safe_response_body(resp),
            }

        return {
            "success": False,
            "apiType": api_type,
            "url": api_url,
            "error": _extract_error_message(resp),
        }

    except (asyncio.TimeoutError, httpx.TimeoutException):
        return {"success": False, "error": "Connection timeout (60s)"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}

