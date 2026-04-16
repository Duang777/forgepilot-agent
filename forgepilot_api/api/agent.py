from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from forgepilot_api.api.utils import SSE_HEADERS, sse_event_stream
from forgepilot_api.models import AgentRequest, ExecuteRequest, TitleRequest
from forgepilot_api.services.agent_service import (
    create_session_async,
    delete_session_async,
    get_plan_async,
    get_session_async,
    respond_to_permission_async,
    run_agent,
    run_execution_phase,
    run_planning_phase,
)
from forgepilot_api.services.chat_service import generate_title, run_chat
from forgepilot_api.storage.repositories import (
    create_message as repo_create_message,
    create_session as repo_create_session,
    get_task as repo_get_task,
    reserve_next_task_index as repo_reserve_next_task_index,
    upsert_file_by_path as repo_upsert_file_by_path,
    upsert_task as repo_upsert_task,
    update_task as repo_update_task,
)

router = APIRouter(prefix="/agent", tags=["agent"])
_task_tool_use_context: dict[str, dict[str, dict[str, Any]]] = {}
_ABS_WINDOWS_PATH_RE = re.compile(r"([A-Za-z]:\\[^\r\n`\"]+\.[A-Za-z0-9]{1,12})")
_ABS_POSIX_PATH_RE = re.compile(r"(/[^ \r\n`\"']+\.[A-Za-z0-9]{1,12})")
_WRITE_OUTPUT_PATH_RE = re.compile(r"File (?:written|edited):\s*(.+?)(?:\s*\(|$)", re.IGNORECASE)
_KNOWN_FILE_EXTS = {
    "html",
    "htm",
    "css",
    "js",
    "ts",
    "tsx",
    "jsx",
    "json",
    "md",
    "txt",
    "py",
    "yaml",
    "yml",
    "csv",
    "xml",
    "pdf",
    "docx",
    "xlsx",
    "pptx",
    "png",
    "jpg",
    "jpeg",
    "gif",
    "webp",
    "svg",
}


def _infer_file_type(path: str) -> str:
    ext = Path(path).suffix.lower().lstrip(".")
    if ext in {"py", "js", "ts", "tsx", "jsx", "java", "go", "rs", "cpp", "c", "h", "rb", "php", "swift", "kt"}:
        return "code"
    if ext in {"jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "ico"}:
        return "image"
    if ext in {"ppt", "pptx", "key", "odp"}:
        return "presentation"
    if ext in {"xls", "xlsx", "numbers", "ods"}:
        return "spreadsheet"
    if ext in {"md", "pdf", "doc", "docx", "txt", "rtf", "odt"}:
        return "document"
    if ext in {"json", "yaml", "yml", "xml", "toml", "ini", "conf", "cfg", "env", "csv", "tsv"}:
        return "text"
    if ext in {"html", "htm"}:
        return "website"
    return "text"


def _clean_path_token(raw: str) -> str:
    text = str(raw or "").strip().strip("`").strip("'").strip('"').strip()
    text = text.rstrip(").,;")
    return text


def _normalize_abs_path(raw: str) -> str | None:
    candidate = _clean_path_token(raw)
    if not candidate:
        return None
    try:
        path = Path(candidate).expanduser()
    except Exception:
        return None
    if not path.is_absolute():
        return None
    suffix = path.suffix.lower().lstrip(".")
    if suffix and suffix in _KNOWN_FILE_EXTS:
        return str(path)
    return str(path) if suffix else None


def _extract_paths_from_tool_context(tool_name: str, tool_input: dict[str, Any], tool_output: str) -> list[str]:
    candidates: list[str] = []
    lowered_name = tool_name.lower()

    if lowered_name in {"write", "edit", "multiedit", "notebookedit"}:
        out_match = _WRITE_OUTPUT_PATH_RE.search(tool_output or "")
        if out_match:
            candidates.append(out_match.group(1))
        for key in ("file_path", "path"):
            if isinstance(tool_input.get(key), str):
                candidates.append(str(tool_input.get(key)))

    if lowered_name in {"bash", "sandbox_run_script"}:
        output = tool_output or ""
        for match in _ABS_WINDOWS_PATH_RE.findall(output):
            candidates.append(match)
        for match in _ABS_POSIX_PATH_RE.findall(output):
            candidates.append(match)

    deduped: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        normalized = _normalize_abs_path(raw)
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


async def _record_file_artifacts_from_tool_result(task_id: str, event: dict[str, Any]) -> None:
    if event.get("isError"):
        return
    tool_use_id = str(event.get("toolUseId") or "").strip()
    if not tool_use_id:
        return

    task_ctx = _task_tool_use_context.get(task_id, {})
    tool_ctx = task_ctx.get(tool_use_id)
    if not isinstance(tool_ctx, dict):
        return

    tool_name = str(tool_ctx.get("name") or "")
    tool_input = tool_ctx.get("input") if isinstance(tool_ctx.get("input"), dict) else {}
    tool_output = str(event.get("output") or "")
    paths = _extract_paths_from_tool_context(tool_name, tool_input, tool_output)

    for path in paths:
        name = Path(path).name or path
        await repo_upsert_file_by_path(
            task_id=task_id,
            path=path,
            name=name,
            file_type=_infer_file_type(path),
            preview=(tool_output[:500] if tool_output else None),
        )


async def _persist_agent_event(task_id: str, event: dict, prompt: str | None = None, session_id: str | None = None) -> None:
    if prompt is not None and session_id:
        existing_task = await repo_get_task(task_id)
        if not existing_task:
            task_index = await repo_reserve_next_task_index(session_id, prompt)
            await repo_upsert_task(
                task_id,
                session_id=session_id,
                task_index=task_index,
                prompt=prompt,
                status="running",
            )
        else:
            await repo_create_session(session_id, prompt)

    etype = event.get("type")
    if etype == "text":
        await repo_create_message(task_id=task_id, msg_type="text", content=event.get("content"))
    elif etype == "tool_use":
        tool_use_id = str(event.get("id") or "").strip()
        if tool_use_id:
            _task_tool_use_context.setdefault(task_id, {})[tool_use_id] = {
                "name": event.get("name"),
                "input": event.get("input") if isinstance(event.get("input"), dict) else {},
            }
        await repo_create_message(
            task_id=task_id,
            msg_type="tool_use",
            tool_name=event.get("name"),
            tool_input=event.get("input") or {},
            tool_use_id=event.get("id"),
        )
    elif etype == "tool_result":
        await repo_create_message(
            task_id=task_id,
            msg_type="tool_result",
            tool_output=event.get("output"),
            tool_use_id=event.get("toolUseId"),
            error_message=event.get("output") if event.get("isError") else None,
        )
        await _record_file_artifacts_from_tool_result(task_id, event)
    elif etype == "result":
        await repo_create_message(
            task_id=task_id,
            msg_type="result",
            subtype=event.get("subtype"),
            content=event.get("content"),
        )
        status = "completed" if event.get("subtype") == "success" else "error"
        await repo_update_task(
            task_id,
            status=status,
            cost=event.get("cost"),
            duration=event.get("duration"),
        )
        if status != "running":
            _task_tool_use_context.pop(task_id, None)
    elif etype == "error":
        await repo_create_message(task_id=task_id, msg_type="error", error_message=event.get("message"))
        await repo_update_task(task_id, status="error")
        _task_tool_use_context.pop(task_id, None)
    elif etype == "permission_request":
        await repo_create_message(
            task_id=task_id,
            msg_type="permission_request",
            content=json.dumps(event.get("permission") or {}, ensure_ascii=False),
        )


@router.post("/chat")
async def post_chat(body: AgentRequest) -> StreamingResponse:
    if not body.prompt:
        return JSONResponse({"error": "prompt is required"}, status_code=400)
    stream = sse_event_stream(
        run_chat(
            body.prompt,
            model_config=body.modelConfig,
            language=body.language,
            conversation=body.conversation,
        )
    )
    return StreamingResponse(stream, headers=SSE_HEADERS)


@router.post("/plan")
async def post_plan(body: AgentRequest) -> StreamingResponse:
    if not body.prompt:
        return JSONResponse({"error": "prompt is required"}, status_code=400)
    session = await create_session_async("plan")
    stream = sse_event_stream(
        run_planning_phase(
            body.prompt,
            session,
            model_config=body.modelConfig,
            language=body.language,
        )
    )
    return StreamingResponse(stream, headers=SSE_HEADERS)


@router.post("/execute")
async def post_execute(body: ExecuteRequest) -> StreamingResponse:
    if not body.planId:
        return JSONResponse({"error": "planId is required"}, status_code=400)
    if not await get_plan_async(body.planId):
        return JSONResponse({"error": "Plan not found or expired"}, status_code=404)
    session = await create_session_async("execute")
    async def _gen():
        async for event in run_execution_phase(
            body.planId,
            session,
            original_prompt=body.prompt or "",
            work_dir=body.workDir,
            task_id=body.taskId,
            model_config=body.modelConfig,
            sandbox_config=body.sandboxConfig.model_dump() if body.sandboxConfig else None,
            skills_config=body.skillsConfig.model_dump() if body.skillsConfig else None,
            mcp_config=body.mcpConfig.model_dump() if body.mcpConfig else None,
            language=body.language,
        ):
            if body.taskId:
                await _persist_agent_event(
                    body.taskId,
                    event,
                    prompt=body.prompt or "",
                    session_id=session.id,
                )
            yield event

    stream = sse_event_stream(_gen())
    return StreamingResponse(stream, headers=SSE_HEADERS)


@router.post("")
async def post_agent(body: AgentRequest) -> StreamingResponse:
    if not body.prompt:
        return JSONResponse({"error": "prompt is required"}, status_code=400)
    session = await create_session_async("execute")
    async def _gen():
        async for event in run_agent(
            body.prompt,
            session,
            conversation=[m.model_dump() for m in body.conversation] if body.conversation else None,
            work_dir=body.workDir,
            task_id=body.taskId,
            model_config=body.modelConfig,
            sandbox_config=body.sandboxConfig.model_dump() if body.sandboxConfig else None,
            images=[img.model_dump() for img in body.images] if body.images else None,
            skills_config=body.skillsConfig.model_dump() if body.skillsConfig else None,
            mcp_config=body.mcpConfig.model_dump() if body.mcpConfig else None,
            language=body.language,
        ):
            if body.taskId:
                await _persist_agent_event(
                    body.taskId,
                    event,
                    prompt=body.prompt,
                    session_id=session.id,
                )
            yield event

    stream = sse_event_stream(_gen())
    return StreamingResponse(stream, headers=SSE_HEADERS)


@router.post("/title")
async def post_title(body: TitleRequest) -> dict[str, str]:
    title = await generate_title(body.prompt, model_config=body.modelConfig, language=body.language)
    return {"title": title}


@router.post("/permission")
async def post_permission(body: dict) -> dict[str, str | bool]:
    session_id = str(body.get("sessionId") or "").strip()
    permission_id = str(body.get("permissionId") or "").strip()
    approved = bool(body.get("approved"))
    if not session_id or not permission_id:
        return JSONResponse({"error": "sessionId and permissionId are required"}, status_code=400)

    ok = await respond_to_permission_async(session_id, permission_id, approved)
    if not ok:
        return {"success": False, "message": "No pending permission found"}
    return {"success": True, "message": "Permission response received"}


@router.post("/stop/{session_id}")
async def post_stop(session_id: str) -> dict[str, str]:
    if not await get_session_async(session_id):
        return JSONResponse({"error": "Session not found"}, status_code=404)
    await delete_session_async(session_id)
    return {"status": "stopped"}


@router.get("/session/{session_id}")
async def get_session_status(session_id: str) -> dict:
    session = await get_session_async(session_id)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    return {
        "id": session.id,
        "createdAt": session.created_at.isoformat(),
        "phase": session.phase,
        "isAborted": session.abort_event.is_set(),
    }


@router.get("/plan/{plan_id}")
async def get_plan_by_id(plan_id: str) -> dict:
    plan = await get_plan_async(plan_id)
    if not plan:
        return JSONResponse({"error": "Plan not found"}, status_code=404)
    return plan

