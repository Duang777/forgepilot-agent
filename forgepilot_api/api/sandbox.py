from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from forgepilot_api.api.utils import SSE_HEADERS
from forgepilot_api.sandbox import (
    SANDBOX_IMAGES,
    acquire_provider_with_fallback,
    get_pool_stats,
    get_sandbox_info,
    get_sandbox_registry,
    stop_all_providers,
)
from forgepilot_api.sandbox.types import SandboxExecOptions, ScriptOptions, VolumeMount
from forgepilot_api.services.provider_service import get_config as get_provider_config

router = APIRouter(prefix="/sandbox", tags=["sandbox"])


def _isolation_label(isolation: str) -> str:
    if isolation == "vm":
        return "VM isolation"
    if isolation == "container":
        return "Container isolation"
    if isolation == "process":
        return "Process isolation"
    return "No isolation"


def _provider_payload(provider) -> dict[str, Any]:
    caps = provider.get_capabilities()
    return {
        "provider": provider.type,
        "providerName": provider.name,
        "providerInfo": {
            "type": provider.type,
            "name": provider.name,
            "isolation": caps.isolation,
            "isolationLabel": _isolation_label(caps.isolation),
        },
    }


def _error_result(error: str) -> dict[str, Any]:
    return {
        "success": False,
        "error": error,
        "exitCode": 1,
        "stdout": "",
        "stderr": error,
        "duration": 0,
    }


@router.get("/debug/codex-paths")
async def debug_codex_paths() -> dict[str, Any]:
    registry = get_sandbox_registry()
    codex = await registry.get_instance("codex")
    available = await codex.is_available()
    return {
        "platform": os.name,
        "cwd": os.getcwd(),
        "provider": "codex",
        "available": available,
        "message": "Codex provider availability check",
    }


@router.get("/available")
async def available() -> dict[str, Any]:
    return await get_sandbox_info()


@router.get("/images")
async def images() -> dict[str, Any]:
    return {"images": SANDBOX_IMAGES, "default": SANDBOX_IMAGES["node"]}


@router.get("/pool/stats")
async def pool_stats() -> dict[str, Any]:
    enabled = os.getenv("FORGEPILOT_SANDBOX_POOL_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    return {
        "enabled": enabled,
        "pools": get_pool_stats(),
    }


@router.post("/exec")
async def exec_command(body: dict) -> dict[str, Any]:
    command = body.get("command")
    if not command:
        return JSONResponse({"error": "Command is required"}, status_code=400)

    try:
        cfg = await get_provider_config()
        explicit_provider = body.get("provider")
        preferred_provider = explicit_provider or cfg.get("sandbox", {}).get("type")
        image = body.get("image") or SANDBOX_IMAGES["node"]
        lease = await acquire_provider_with_fallback(
            preferred_provider,
            image=image,
            pool_config=body.get("providerConfig"),
        )
        provider = lease.provider
        try:
            result = await provider.exec(
                SandboxExecOptions(
                    command=str(command),
                    args=[str(x) for x in (body.get("args") or [])],
                    cwd=str(body.get("cwd") or os.getcwd()),
                    env={str(k): str(v) for k, v in (body.get("env") or {}).items()},
                    timeout=int(body.get("timeout") or 120000),
                    image=image,
                )
            )
        finally:
            lease.release()

        return {
            "success": result.exit_code == 0,
            **_provider_payload(provider),
            "usedFallback": lease.used_fallback,
            "fallbackReason": lease.fallback_reason,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exitCode": result.exit_code,
            "duration": result.duration,
        }
    except Exception as exc:
        return JSONResponse(_error_result(str(exc)), status_code=500)


@router.post("/run/file")
async def run_file(body: dict) -> dict[str, Any]:
    file_path = body.get("filePath")
    work_dir = body.get("workDir")
    if not file_path or not work_dir:
        return JSONResponse({"error": "filePath and workDir are required"}, status_code=400)

    try:
        cfg = await get_provider_config()
        explicit_provider = body.get("provider")
        preferred_provider = explicit_provider or cfg.get("sandbox", {}).get("type")

        ext = Path(str(file_path)).suffix.lower()
        runtime = "python" if ext == ".py" else "bun" if ext in {".ts", ".mts"} else "node"
        image = body.get("image") or SANDBOX_IMAGES.get(runtime, SANDBOX_IMAGES["node"])

        packages = [str(x) for x in (body.get("packages") or [])]
        network_packages = [
            "requests",
            "httpx",
            "aiohttp",
            "urllib3",
            "beautifulsoup4",
            "bs4",
            "scrapy",
            "selenium",
            "playwright",
            "httplib2",
            "pycurl",
            "axios",
            "node-fetch",
            "got",
            "superagent",
            "puppeteer",
        ]
        needs_network = any(
            network_name in package.lower()
            for package in packages
            for network_name in network_packages
        )
        effective_provider = preferred_provider
        if not explicit_provider and needs_network:
            effective_provider = "native"

        lease = await acquire_provider_with_fallback(
            effective_provider,
            image=image,
            pool_config=body.get("providerConfig"),
        )
        provider = lease.provider
        try:
            provider.set_volumes([VolumeMount(host_path=str(work_dir), guest_path="/workspace", read_only=False)])
            result = await provider.run_script(
                str(file_path),
                str(work_dir),
                ScriptOptions(
                    args=[str(x) for x in (body.get("args") or [])],
                    env={str(k): str(v) for k, v in (body.get("env") or {}).items()},
                    timeout=int(body.get("timeout") or 120000),
                    packages=packages,
                ),
            )
        finally:
            lease.release()

        return {
            "success": result.exit_code == 0,
            "runtime": runtime,
            **_provider_payload(provider),
            "usedFallback": lease.used_fallback,
            "fallbackReason": lease.fallback_reason,
            "exitCode": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration": result.duration,
        }
    except Exception as exc:
        return JSONResponse(_error_result(str(exc)), status_code=500)


async def _run_script_content(script: str, runtime: str, body: dict) -> Any:
    try:
        cfg = await get_provider_config()
        preferred_provider = body.get("provider") or cfg.get("sandbox", {}).get("type")
        image = body.get("image") or SANDBOX_IMAGES.get(runtime, SANDBOX_IMAGES["node"])
        lease = await acquire_provider_with_fallback(
            preferred_provider,
            image=image,
            pool_config=body.get("providerConfig"),
        )
        provider = lease.provider

        work_dir = str(body.get("cwd") or tempfile.gettempdir())
        suffix = ".py" if runtime == "python" else ".js"
        temp_file = Path(work_dir) / f"temp_script_{int(time.time() * 1000)}{suffix}"
        temp_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file.write_text(script, encoding="utf-8")

        try:
            result = await provider.run_script(
                str(temp_file),
                work_dir,
                ScriptOptions(
                    env={str(k): str(v) for k, v in (body.get("env") or {}).items()},
                    timeout=int(body.get("timeout") or 120000),
                    packages=[str(x) for x in (body.get("packages") or [])],
                ),
            )
        finally:
            lease.release()
            try:
                temp_file.unlink(missing_ok=True)
            except Exception:
                pass

        return {
            "success": result.exit_code == 0,
            **_provider_payload(provider),
            "usedFallback": lease.used_fallback,
            "fallbackReason": lease.fallback_reason,
            "exitCode": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration": result.duration,
        }
    except Exception as exc:
        return JSONResponse(_error_result(str(exc)), status_code=500)


@router.post("/run/node")
async def run_node(body: dict) -> Any:
    script = body.get("script")
    if not script:
        return JSONResponse({"error": "Script content is required"}, status_code=400)
    return await _run_script_content(str(script), "node", body)


@router.post("/run/python")
async def run_python(body: dict) -> Any:
    script = body.get("script")
    if not script:
        return JSONResponse({"error": "Script content is required"}, status_code=400)
    return await _run_script_content(str(script), "python", body)


@router.post("/exec/stream")
async def exec_stream(body: dict) -> Any:
    command = body.get("command")
    if not command:
        return JSONResponse({"error": "Command is required"}, status_code=400)

    async def _generator():
        lease = None
        try:
            cfg = await get_provider_config()
            preferred = body.get("provider") or cfg.get("sandbox", {}).get("type")
            image = body.get("image") or SANDBOX_IMAGES["node"]
            lease = await acquire_provider_with_fallback(
                preferred,
                image=image,
                pool_config=body.get("providerConfig"),
            )
            provider = lease.provider
            started = {
                "type": "started",
                **_provider_payload(provider),
                "usedFallback": lease.used_fallback,
                "fallbackReason": lease.fallback_reason,
            }
            yield f"data: {json.dumps(started, ensure_ascii=False)}\n\n"

            result = await provider.exec(
                SandboxExecOptions(
                    command=str(command),
                    args=[str(x) for x in (body.get("args") or [])],
                    cwd=str(body.get("cwd") or os.getcwd()),
                    env={str(k): str(v) for k, v in (body.get("env") or {}).items()},
                    timeout=int(body.get("timeout") or 120000),
                    image=image,
                )
            )

            for line in (result.stdout or "").splitlines():
                yield f"data: {json.dumps({'type': 'stdout', 'content': line}, ensure_ascii=False)}\n\n"
            for line in (result.stderr or "").splitlines():
                yield f"data: {json.dumps({'type': 'stderr', 'content': line}, ensure_ascii=False)}\n\n"

            done = {"type": "done", "exitCode": result.exit_code, "duration": result.duration}
            yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
        except Exception as exc:
            err = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
        finally:
            if lease is not None:
                lease.release()

    return StreamingResponse(_generator(), headers=SSE_HEADERS)


@router.post("/stop-all")
async def stop_all() -> dict[str, Any]:
    try:
        await stop_all_providers()
        return {"success": True, "message": "All sandbox providers stopped"}
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

