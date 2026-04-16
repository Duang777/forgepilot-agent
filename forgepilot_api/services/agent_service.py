from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator
from urllib.parse import quote

from forgepilot_sdk import AgentOptions, create_agent
from forgepilot_api.config import WORK_DIR, get_all_mcp_config_paths, get_all_skills_dirs
from forgepilot_api.models import ModelConfig, TaskPlan
from forgepilot_api.core.logging import get_log_file_path, get_logger
from forgepilot_api.services.codex_config_service import load_codex_runtime_config
from forgepilot_api.services.provider_service import get_config as get_provider_config
from forgepilot_api.services.runtime_state_service import (
    create_runtime_session,
    delete_expired_runtime_permissions,
    delete_expired_runtime_plans,
    delete_runtime_permission,
    delete_runtime_plan,
    get_runtime_permission,
    get_runtime_plan,
    get_runtime_session,
    publish_runtime_permission_event,
    register_runtime_permission,
    save_runtime_plan,
    set_runtime_permission_status,
    set_runtime_session_aborted,
    wait_runtime_permission_event,
)
from forgepilot_api.core.telemetry import add_span_event, start_span

DEFAULT_MODEL = "claude-sonnet-4-20250514"
MAX_HISTORY_TOKENS = 2000
MIN_MESSAGES_TO_KEEP = 3
LOG_FILE_PATH = get_log_file_path()
RUNTIME_PLAN_TTL_SECONDS = int(os.getenv("FORGEPILOT_RUNTIME_PLAN_TTL_SECONDS", "3600"))
RUNTIME_PERMISSION_TTL_SECONDS = int(os.getenv("FORGEPILOT_RUNTIME_PERMISSION_TTL_SECONDS", "1800"))
PERMISSION_DECISION_TIMEOUT_SECONDS = int(os.getenv("FORGEPILOT_PERMISSION_DECISION_TIMEOUT_SECONDS", "1800"))
PERMISSION_POLL_INTERVAL_SECONDS = max(
    0.1,
    float(os.getenv("FORGEPILOT_PERMISSION_POLL_INTERVAL_SECONDS", "0.5")),
)

PLANNING_INSTRUCTION = """You are an AI assistant in planning mode.
First decide whether the request is simple chat/question or a task requiring tools/files.

INTENT RULES:
- SIMPLE chat/question -> direct_answer
- COMPLEX task (file operations, coding, script execution, document creation, search, multi-step work) -> plan
- If the user asks to create/modify/delete/write files, you MUST return type="plan"

If simple chat/question, output strict JSON:
{"type":"direct_answer","answer":"..."}

If task execution is needed, output strict JSON:
{"type":"plan","goal":"...","steps":[{"id":"1","description":"..."},{"id":"2","description":"..."},{"id":"3","description":"..."}],"notes":"..."}

Rules:
- Output JSON only.
- Do not include implementation code or file content.
- Steps must be concise and action-oriented.
- For plans, describe WHAT to do, not HOW to implement it.
"""

_API_KEY_ERROR_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"invalid api key",
        r"invalid_api_key",
        r"authentication.*fail",
        r"unauthorized",
        r"\b401\b",
        r"\b403\b",
        r"api key is required",
    ]
]

_TASK_REQUEST_PATTERNS = [
    re.compile(
        r"\b(create|write|modify|edit|update|delete|remove|rename|implement|build|generate|fix|refactor|run|execute|search|analyze)\b",
        re.IGNORECASE,
    ),
    re.compile(r"(文件|写入|新建|创建|生成|修改|编辑|删除|实现|修复|重构|执行|运行|保存|代码|脚本|页面|html|文档|表格|演示|搜索)"),
]

_SIMPLE_CHAT_PATTERNS = [
    re.compile(r"^\s*(hi|hello|hey|你好|嗨)\b", re.IGNORECASE),
    re.compile(r"(who are you|what can you do|你是谁|你能做什么)", re.IGNORECASE),
]

_FILE_TASK_PATTERNS = [
    re.compile(r"\b(create|write|generate|modify|edit|save|file|html|js|css|markdown|md)\b", re.IGNORECASE),
    re.compile(r"(生成|创建|新建|写入|保存|修改|编辑|文件|页面|代码|脚本|html|路径)"),
]

_PATH_QUERY_PATTERNS = [
    re.compile(r"\b(path|where|location|exists?|created)\b", re.IGNORECASE),
    re.compile(r"(路径|在哪|位置|是否存在|有没有生成|生成了吗|给我.*路径)"),
]

_WRITE_LIKE_TOOL_NAMES = {
    "write",
    "edit",
    "multiedit",
    "bash",
    "sandbox_run_script",
}

_VERIFY_LIKE_TOOL_NAMES = {
    "read",
    "bash",
    "glob",
    "ls",
}


@dataclass(slots=True)
class AgentSession:
    id: str
    created_at: datetime
    phase: str
    abort_event: asyncio.Event


_local_sessions: dict[str, AgentSession] = {}
_local_plans: dict[str, dict[str, Any]] = {}
logger = get_logger(__name__)


def _build_session(session_id: str, phase: str, created_at: datetime, aborted: bool = False) -> AgentSession:
    session = AgentSession(id=session_id, created_at=created_at, phase=phase, abort_event=asyncio.Event())
    if aborted:
        session.abort_event.set()
    return session


def _parse_datetime(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return datetime.utcnow()
    return datetime.utcnow()


async def create_session_async(phase: str = "plan") -> AgentSession:
    session_id = str(uuid.uuid4())
    session = _build_session(session_id, phase, datetime.utcnow(), aborted=False)
    _local_sessions[session_id] = session
    await create_runtime_session(session_id, phase)
    return session


def create_session(phase: str = "plan") -> AgentSession:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(create_session_async(phase))
    session_id = str(uuid.uuid4())
    session = _build_session(session_id, phase, datetime.utcnow(), aborted=False)
    _local_sessions[session_id] = session
    # Avoid fire-and-forget DB tasks on transient event loops.
    # Async call sites should use create_session_async() for persisted runtime state.
    return session


async def get_session_async(session_id: str) -> AgentSession | None:
    local = _local_sessions.get(session_id)
    row = await get_runtime_session(session_id)
    if not row:
        return local
    aborted = bool(row.get("aborted"))
    if local:
        if aborted:
            local.abort_event.set()
        return local
    phase = str(row.get("phase") or "execute")
    created_at = _parse_datetime(row.get("created_at"))
    session = _build_session(session_id, phase, created_at, aborted=aborted)
    _local_sessions[session_id] = session
    return session


def get_session(session_id: str) -> AgentSession | None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(get_session_async(session_id))
    return _local_sessions.get(session_id)


async def delete_session_async(session_id: str) -> bool:
    local = _local_sessions.pop(session_id, None)
    if local:
        local.abort_event.set()
    changed = await set_runtime_session_aborted(session_id, True)
    return bool(changed or local is not None)


def delete_session(session_id: str) -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(delete_session_async(session_id))
    local = _local_sessions.get(session_id)
    if local:
        local.abort_event.set()
    # Avoid background runtime-state writes on ephemeral loops.
    _local_sessions.pop(session_id, None)
    return local is not None


async def stop_agent_async(session_id: str) -> None:
    session = await get_session_async(session_id)
    if session:
        session.abort_event.set()
    await set_runtime_session_aborted(session_id, True)


def stop_agent(session_id: str) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(stop_agent_async(session_id))
        return
    local = _local_sessions.get(session_id)
    if local:
        local.abort_event.set()
    loop.create_task(stop_agent_async(session_id))


async def _register_permission_request(session_id: str, permission: dict[str, Any]) -> None:
    permission_id = str(permission.get("id") or "")
    if not permission_id:
        return
    await delete_expired_runtime_permissions()
    await register_runtime_permission(
        session_id=session_id,
        permission_id=permission_id,
        payload=permission,
        ttl_seconds=RUNTIME_PERMISSION_TTL_SECONDS,
    )


async def _is_session_aborted(session: AgentSession) -> bool:
    if session.abort_event.is_set():
        return True
    row = await get_runtime_session(session.id)
    if not row:
        return False
    aborted = bool(row.get("aborted"))
    if aborted:
        session.abort_event.set()
    return aborted


async def _wait_for_permission_decision(session_id: str, permission_id: str) -> bool:
    deadline = time.monotonic() + PERMISSION_DECISION_TIMEOUT_SECONDS
    while True:
        row = await get_runtime_permission(session_id, permission_id)
        if not row:
            return False
        status = str(row.get("status") or "pending").lower()
        if status == "approved":
            await delete_runtime_permission(session_id, permission_id)
            return True
        if status in {"denied", "timeout", "expired", "cancelled"}:
            await delete_runtime_permission(session_id, permission_id)
            return False

        session = await get_session_async(session_id)
        if session and await _is_session_aborted(session):
            await set_runtime_permission_status(session_id, permission_id, "cancelled")
            await publish_runtime_permission_event(
                session_id=session_id,
                permission_id=permission_id,
                status="cancelled",
            )
            await delete_runtime_permission(session_id, permission_id)
            return False

        if time.monotonic() >= deadline:
            await set_runtime_permission_status(session_id, permission_id, "timeout")
            await publish_runtime_permission_event(
                session_id=session_id,
                permission_id=permission_id,
                status="timeout",
            )
            await delete_runtime_permission(session_id, permission_id)
            return False

        remaining = max(0.0, deadline - time.monotonic())
        wait_seconds = min(PERMISSION_POLL_INTERVAL_SECONDS, remaining)
        event_status = await wait_runtime_permission_event(
            session_id=session_id,
            permission_id=permission_id,
            timeout_seconds=max(0.05, wait_seconds),
        )
        if event_status in {"approved", "denied", "timeout", "expired", "cancelled"}:
            continue


async def respond_to_permission_async(session_id: str, permission_id: str, approved: bool) -> bool:
    row = await get_runtime_permission(session_id, permission_id)
    if not row:
        return False
    status = str(row.get("status") or "pending").lower()
    if status != "pending":
        return False
    next_status = "approved" if approved else "denied"
    changed = await set_runtime_permission_status(session_id, permission_id, next_status)
    if changed:
        await publish_runtime_permission_event(
            session_id=session_id,
            permission_id=permission_id,
            status=next_status,
        )
    return changed


def respond_to_permission(session_id: str, permission_id: str, approved: bool) -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(respond_to_permission_async(session_id, permission_id, approved))
    return False


async def get_plan_async(plan_id: str) -> dict[str, Any] | None:
    await delete_expired_runtime_plans()
    row = await get_runtime_plan(plan_id)
    if not row:
        _local_plans.pop(plan_id, None)
        return None
    payload = row.get("payload")
    if isinstance(payload, dict):
        _local_plans[plan_id] = payload
        return payload
    return None


def get_plan(plan_id: str) -> dict[str, Any] | None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(get_plan_async(plan_id))
    return _local_plans.get(plan_id)


async def save_plan_async(plan: dict[str, Any]) -> None:
    plan_id = str(plan.get("id") or "")
    if not plan_id:
        raise ValueError("plan.id is required")
    _local_plans[plan_id] = plan
    await save_runtime_plan(plan, ttl_seconds=RUNTIME_PLAN_TTL_SECONDS)


def save_plan(plan: dict[str, Any]) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(save_plan_async(plan))
        return
    plan_id = str(plan.get("id") or "")
    if plan_id:
        _local_plans[plan_id] = plan
    # Async call sites should use save_plan_async() when runtime persistence is required.


async def delete_plan_async(plan_id: str) -> bool:
    _local_plans.pop(plan_id, None)
    return await delete_runtime_plan(plan_id)


def delete_plan(plan_id: str) -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(delete_plan_async(plan_id))
    _local_plans.pop(plan_id, None)
    return False


def _expand_path(input_path: str) -> str:
    text = input_path or str(WORK_DIR)
    expanded = str(Path(text).expanduser())
    if __import__("os").name == "nt":
        return expanded.replace("/", "\\")
    return expanded


def _generate_fallback_slug(prompt: str, task_id: str) -> str:
    slug = re.sub(r"[\u4e00-\u9fff]", "", prompt.lower())
    slug = re.sub(r"[^\w\s-]", " ", slug)
    slug = re.sub(r"\s+", "-", slug).strip("-")
    slug = slug[:50].rstrip("-")
    if not slug or len(slug) < 3:
        slug = "task"
    return f"{slug}-{task_id[-6:]}"


def _get_session_work_dir(work_dir: str | None = None, prompt: str | None = None, task_id: str | None = None) -> str:
    expanded = _expand_path(work_dir or str(WORK_DIR))
    has_sessions_path = "/sessions/" in expanded or "\\sessions\\" in expanded
    ends_with_sessions = expanded.endswith("/sessions") or expanded.endswith("\\sessions")
    if has_sessions_path and not ends_with_sessions:
        return expanded

    sessions_dir = str(Path(expanded) / "sessions")
    if prompt and task_id:
        folder = _generate_fallback_slug(prompt, task_id)
    elif task_id:
        folder = task_id
    else:
        folder = f"session-{int(__import__('time').time() * 1000)}"
    return str(Path(sessions_dir) / folder)


def _estimate_token_count(text: str) -> int:
    return max(1, int(len(text) / 4))


def _format_conversation_history(conversation: list[dict[str, Any]] | None) -> str:
    if not conversation:
        return ""

    formatted: list[str] = []
    for message in conversation:
        role = "User" if message.get("role") == "user" else "Assistant"
        content = str(message.get("content") or "")
        formatted.append(f"{role}: {content}")

    with_tokens = [{"content": c, "tokens": _estimate_token_count(c)} for c in formatted]
    total = 0
    selected: list[str] = []
    start_index = max(0, len(with_tokens) - MIN_MESSAGES_TO_KEEP)

    for idx in range(len(with_tokens) - 1, start_index - 1, -1):
        item = with_tokens[idx]
        if total + item["tokens"] <= MAX_HISTORY_TOKENS:
            selected.insert(0, item["content"])
            total += item["tokens"]
        else:
            break

    for idx in range(start_index - 1, -1, -1):
        item = with_tokens[idx]
        if total + item["tokens"] <= MAX_HISTORY_TOKENS:
            selected.insert(0, item["content"])
            total += item["tokens"]
        else:
            break

    if not selected:
        return ""

    context = "\n\n".join(selected)
    truncated = len(selected) < len(formatted)
    notice = f"\n\n[Note: Showing {len(selected)} of {len(formatted)} messages.]" if truncated else ""
    return f"## Previous Conversation Context\n\n{context}{notice}\n\n---\n## Current Request\n"


def _detect_language_from_text(text: str | None) -> str:
    if not text:
        return "en-US"
    return "zh-CN" if re.search(r"[\u3400-\u9fff]", text) else "en-US"


def _resolve_language(language: str | None, prompt: str | None) -> str:
    mapping = {
        "en": "en-US",
        "en-us": "en-US",
        "english": "en-US",
        "zh": "zh-CN",
        "zh-cn": "zh-CN",
        "zh-hans": "zh-CN",
        "chinese": "zh-CN",
        "cn": "zh-CN",
    }
    if language:
        key = language.strip().lower()
        if key in mapping:
            return mapping[key]
        if key.startswith("zh"):
            return "zh-CN"
        if key.startswith("en"):
            return "en-US"
    return _detect_language_from_text(prompt)


def _build_language_instruction(language: str | None, prompt: str | None) -> str:
    resolved = _resolve_language(language, prompt)
    if resolved == "zh-CN":
        return "\n## LANGUAGE REQUIREMENT\n- Output language: Chinese (Simplified)\n- Respond only in Simplified Chinese.\n"
    return "\n## LANGUAGE REQUIREMENT\n- Output language: English\n- Respond only in English.\n"


def _looks_like_task_request(prompt: str | None) -> bool:
    if not prompt:
        return False
    text = str(prompt).strip()
    if not text:
        return False
    if any(pattern.search(text) for pattern in _SIMPLE_CHAT_PATTERNS):
        return False
    return any(pattern.search(text) for pattern in _TASK_REQUEST_PATTERNS)


def _build_fallback_plan_from_prompt(prompt: str) -> dict[str, Any]:
    compact_goal = re.sub(r"\s+", " ", prompt).strip()
    if len(compact_goal) > 120:
        compact_goal = f"{compact_goal[:117]}..."
    if not compact_goal:
        compact_goal = "Complete the requested task safely in the workspace"
    return {
        "id": str(uuid.uuid4()),
        "goal": compact_goal,
        "steps": [
            {"id": "1", "description": "Inspect target paths and prerequisites", "status": "pending"},
            {"id": "2", "description": "Create or update required files with tools", "status": "pending"},
            {"id": "3", "description": "Verify outputs and summarize completion", "status": "pending"},
        ],
        "notes": "Auto-generated fallback plan because planning response was not actionable.",
        "createdAt": datetime.utcnow().isoformat(),
    }


def _looks_like_file_task(prompt: str | None) -> bool:
    if not prompt:
        return False
    text = str(prompt).strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in _FILE_TASK_PATTERNS)


def _looks_like_path_query(prompt: str | None) -> bool:
    if not prompt:
        return False
    text = str(prompt).strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in _PATH_QUERY_PATTERNS)


def _assistant_claims_file_written(text: str) -> bool:
    compact = str(text or "").strip()
    if not compact:
        return False
    patterns = [
        r"已为你生成",
        r"已经生成",
        r"已创建",
        r"created file",
        r"generated at",
        r"saved to",
        r"\.(html|md|txt|py|js|css)\b",
    ]
    return any(re.search(p, compact, re.IGNORECASE) for p in patterns)


def _should_block_unverified_file_success(prompt: str, tool_names: set[str], assistant_text: str) -> bool:
    normalized_tools = {str(name or "").strip().lower() for name in tool_names if str(name or "").strip()}
    has_write = any(name in _WRITE_LIKE_TOOL_NAMES for name in normalized_tools)
    has_verify = any(name in _VERIFY_LIKE_TOOL_NAMES for name in normalized_tools)

    is_file_task = _looks_like_file_task(prompt)
    is_path_query = _looks_like_path_query(prompt)
    claims_written = _assistant_claims_file_written(assistant_text)

    if is_file_task and not has_write:
        return True
    if is_path_query and claims_written and not (has_write or has_verify):
        return True
    if claims_written and is_file_task and not has_write:
        return True
    return False


def _get_workspace_instruction(work_dir: str, sandbox_enabled: bool) -> str:
    base = f"""
## CRITICAL: Workspace Configuration
MANDATORY OUTPUT DIRECTORY: {work_dir}

Rules:
1. Use absolute paths rooted at {work_dir}
2. Do not write outside this directory
3. Use Read before modifying existing files; new files can be created directly.
4. For non-destructive create/update tasks, execute directly with tools instead of asking for extra confirmation.
5. Never claim files were created/modified unless tool results confirm the action.
"""
    if sandbox_enabled:
        base += """
## Sandbox Mode
Sandbox mode is enabled.
- Prefer sandbox tools to execute scripts
- Do not run script files directly with host Bash when sandbox execution is available
"""
    return base


def _extract_json_object(text: str, start_index: int = 0) -> str | None:
    first = text.find("{", start_index)
    if first < 0:
        return None
    count = 0
    in_string = False
    escape_next = False
    for idx in range(first, len(text)):
        ch = text[idx]
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            count += 1
        elif ch == "}":
            count -= 1
            if count == 0:
                return text[first : idx + 1]
    return None


def _normalize_plan_steps(raw_steps: list[Any]) -> list[dict[str, Any]]:
    valid_steps: list[dict[str, Any]] = []
    for idx, step in enumerate(raw_steps, start=1):
        if not isinstance(step, dict):
            continue
        desc = str(step.get("description") or "").strip()
        desc_lower = desc.lower()
        if (
            len(desc) <= 10
            or "execute the task" in desc_lower
            or "do the work" in desc_lower
            or "complete the request" in desc_lower
        ):
            continue
        valid_steps.append(
            {
                "id": str(step.get("id") or idx),
                "description": desc,
                "status": "pending",
            }
        )

    if valid_steps:
        return valid_steps

    fallback: list[dict[str, Any]] = []
    for idx, step in enumerate(raw_steps, start=1):
        if not isinstance(step, dict):
            continue
        fallback.append(
            {
                "id": str(step.get("id") or idx),
                "description": str(step.get("description") or f"Step {idx}"),
                "status": "pending",
            }
        )
    return fallback


def _parse_plan_from_response(response_text: str) -> dict[str, Any] | None:
    json_text: str | None = None

    code_block = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", response_text)
    if code_block:
        json_text = _extract_json_object(code_block.group(1))

    if not json_text:
        goal_index = response_text.find('"goal"')
        if goal_index >= 0:
            start = goal_index
            while start > 0 and response_text[start] != "{":
                start -= 1
            if response_text[start] == "{":
                json_text = _extract_json_object(response_text, start)

    if not json_text:
        type_index = response_text.find('{"type"')
        if type_index >= 0:
            json_text = _extract_json_object(response_text, type_index)

    if not json_text:
        json_text = _extract_json_object(response_text)

    if not json_text:
        return None

    try:
        parsed = json.loads(json_text)
    except Exception:
        return None

    if not isinstance(parsed, dict):
        return None
    if not parsed.get("goal") or not isinstance(parsed.get("steps"), list):
        return None

    steps = _normalize_plan_steps(parsed["steps"])
    if not steps:
        return None

    return {
        "id": str(uuid.uuid4()),
        "goal": str(parsed.get("goal") or "Unknown goal"),
        "steps": steps,
        "notes": str(parsed.get("notes") or ""),
        "createdAt": datetime.utcnow().isoformat(),
    }


def _parse_planning_response(response_text: str) -> dict[str, Any] | None:
    response = response_text.strip()
    if not response:
        return None

    json_text: str | None = None
    block = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", response)
    if block:
        json_text = _extract_json_object(block.group(1))
    if not json_text:
        type_index = response.find('{"type"')
        if type_index >= 0:
            json_text = _extract_json_object(response, type_index)
    if not json_text:
        json_text = _extract_json_object(response)

    if not json_text:
        if '"steps"' not in response and '"goal"' not in response:
            return {"type": "direct_answer", "answer": response}
        return None

    try:
        parsed = json.loads(json_text)
    except Exception:
        # salvage direct answer from malformed JSON
        answer_match = re.search(r'"answer"\s*:\s*"([\s\S]*?)"\s*[,}]', response)
        if answer_match:
            answer = (
                answer_match.group(1)
                .replace("\\n", "\n")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
                .strip()
            )
            if answer:
                return {"type": "direct_answer", "answer": answer}
        return {"type": "direct_answer", "answer": response}

    if not isinstance(parsed, dict):
        return None
    if parsed.get("type") == "direct_answer" and isinstance(parsed.get("answer"), str):
        return {"type": "direct_answer", "answer": parsed["answer"].strip()}
    if parsed.get("type") == "plan" or (parsed.get("goal") and isinstance(parsed.get("steps"), list)):
        plan = _parse_plan_from_response(response)
        if plan:
            return {"type": "plan", "plan": plan}
    if isinstance(parsed.get("answer"), str):
        return {"type": "direct_answer", "answer": parsed["answer"].strip()}
    return None


def _is_custom_api(model_config: ModelConfig | None) -> bool:
    return bool(model_config and model_config.baseUrl and model_config.apiKey)


def _encode_error_detail(error_message: str) -> str:
    detail = str(error_message or "").strip()
    if not detail:
        return ""

    # Keep details compact and safe for transport in the marker payload.
    detail = re.sub(r"[\r\n]+", " | ", detail)
    detail = re.sub(r"\s+", " ", detail).strip()
    detail = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "sk-***", detail)
    detail = re.sub(r"(?i)(api[_ -]?key\s*[=:]\s*)([^\s,;]+)", r"\1***", detail)
    if len(detail) > 280:
        detail = f"{detail[:280]}..."
    return quote(detail, safe="")


def _sanitize_error(error_message: str, model_config: ModelConfig | None, *, api_key_missing: bool = False) -> str:
    if api_key_missing:
        return "__API_KEY_ERROR__"
    if any(pattern.search(error_message) for pattern in _API_KEY_ERROR_PATTERNS):
        return "__API_KEY_ERROR__"
    if _is_custom_api(model_config):
        encoded_detail = _encode_error_detail(error_message)
        if encoded_detail:
            return f"__CUSTOM_API_ERROR__|{model_config.baseUrl}|{LOG_FILE_PATH}|{encoded_detail}"
        return f"__CUSTOM_API_ERROR__|{model_config.baseUrl}|{LOG_FILE_PATH}"
    encoded_detail = _encode_error_detail(error_message)
    if encoded_detail:
        return f"__INTERNAL_ERROR__|{LOG_FILE_PATH}|{encoded_detail}"
    return f"__INTERNAL_ERROR__|{LOG_FILE_PATH}"


def _load_mcp_servers(mcp_config: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if mcp_config and not bool(mcp_config.get("enabled", True)):
        return {}

    explicit_path = str((mcp_config or {}).get("mcpConfigPath") or "").strip()
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path).expanduser())
    else:
        app_enabled = bool((mcp_config or {}).get("appDirEnabled", True))
        user_enabled = bool((mcp_config or {}).get("userDirEnabled", True))
        for cfg in get_all_mcp_config_paths():
            name = cfg["name"]
            path = Path(cfg["path"]).expanduser()
            if name == "forgepilot" and app_enabled:
                candidates.append(path)
            if name == "claude" and user_enabled:
                candidates.append(path)

    servers: dict[str, dict[str, Any]] = {}
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        raw = data.get("mcpServers", data)
        if isinstance(raw, dict):
            for key, value in raw.items():
                if isinstance(value, dict):
                    servers[str(key)] = value
    return servers


def _resolve_skills_paths(skills_config: dict[str, Any] | None) -> list[str] | None:
    if skills_config and not bool(skills_config.get("enabled", True)):
        return []

    explicit_path = str((skills_config or {}).get("skillsPath") or "").strip()
    if explicit_path:
        return [explicit_path]

    app_enabled = bool((skills_config or {}).get("appDirEnabled", True))
    user_enabled = bool((skills_config or {}).get("userDirEnabled", True))

    paths: list[str] = []
    for cfg in get_all_skills_dirs():
        if cfg["name"] == "forgepilot" and app_enabled:
            paths.append(cfg["path"])
        if cfg["name"] == "claude" and user_enabled:
            paths.append(cfg["path"])
    return paths


async def _resolve_model_config(model_config: ModelConfig | None) -> ModelConfig | None:
    cfg = await get_provider_config()
    agent_cfg = cfg.get("agent", {}).get("config", {}) if isinstance(cfg, dict) else {}
    if not isinstance(agent_cfg, dict):
        agent_cfg = {}

    codex_cfg = load_codex_runtime_config()
    req_api_key = str((model_config.apiKey if model_config else "") or "")
    req_base_url = str((model_config.baseUrl if model_config else "") or "")
    req_model = str((model_config.model if model_config else "") or "")
    req_api_type = str((model_config.apiType if model_config else "") or "")

    api_key = (
        req_api_key
        or str(agent_cfg.get("apiKey") or "")
        or str(codex_cfg.get("apiKey") or "")
    )
    base_url = (
        req_base_url
        or str(agent_cfg.get("baseUrl") or "")
        or str(codex_cfg.get("baseUrl") or "")
        or None
    )
    model = (
        req_model
        or str(agent_cfg.get("model") or "")
        or str(cfg.get("defaultModel") or "")
        or str(codex_cfg.get("model") or "")
        or DEFAULT_MODEL
    )
    api_type = (
        req_api_type
        or str(agent_cfg.get("apiType") or "")
        or str(codex_cfg.get("apiType") or "")
        or ("anthropic-messages" if "claude" in model.lower() else "openai-completions")
    )

    if not api_key:
        return None

    return ModelConfig(apiKey=api_key, baseUrl=base_url, model=model, apiType=api_type)


def _build_agent_options(
    model_config: ModelConfig,
    cwd: str,
    session_id: str,
    *,
    append_system_prompt: str | None = None,
    allowed_tools: list[str] | None = None,
    sandbox_config: dict[str, Any] | None = None,
    skills_paths: list[str] | None = None,
    mcp_servers: dict[str, dict[str, Any]] | None = None,
    on_permission_request: Any | None = None,
    wait_for_permission_decision: Any | None = None,
) -> AgentOptions:
    model = model_config.model or DEFAULT_MODEL
    api_type = model_config.apiType or ("anthropic-messages" if "claude" in model.lower() else "openai-completions")
    permission_mode = os.getenv("FORGEPILOT_PERMISSION_MODE", os.getenv("AGENT_PERMISSION_MODE", "bypassPermissions"))
    return AgentOptions(
        model=model,
        api_type=api_type,
        api_key=model_config.apiKey,
        base_url=model_config.baseUrl,
        cwd=cwd,
        max_turns=200,
        permission_mode=permission_mode if permission_mode else "bypassPermissions",
        session_id=session_id,
        allowed_tools=allowed_tools,
        skills_paths=skills_paths,
        mcp_servers=mcp_servers,
        on_permission_request=on_permission_request,
        wait_for_permission_decision=wait_for_permission_decision,
        append_system_prompt=append_system_prompt,
        persist_session=True,
    )


def _save_images_to_workdir(images: list[dict[str, Any]] | None, work_dir: Path) -> list[str]:
    if not images:
        return []
    work_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for idx, image in enumerate(images):
        try:
            payload = str(image.get("data") or "")
            if "," in payload:
                payload = payload.split(",", 1)[1]
            raw = base64.b64decode(payload, validate=False)
            mime_type = str(image.get("mimeType") or "image/png")
            ext = mime_type.split("/")[-1] if "/" in mime_type else "png"
            filename = f"image_{int(datetime.utcnow().timestamp() * 1000)}_{idx}.{ext}"
            path = work_dir / filename
            path.write_bytes(raw)
            saved.append(str(path))
        except Exception:
            continue
    return saved


async def _map_sdk_event(event: dict[str, Any], tool_names: dict[str, str] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if event.get("type") == "assistant":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "text":
                out.append({"type": "text", "content": block.get("text", "")})
            elif block.get("type") == "tool_use":
                tool_id = block.get("id")
                tool_name = block.get("name")
                if tool_names is not None and tool_id:
                    tool_names[str(tool_id)] = str(tool_name or "unknown")
                out.append(
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": tool_name,
                        "input": block.get("input"),
                    }
                )
    elif event.get("type") == "tool_result":
        result = event.get("result", {})
        tool_use_id = result.get("tool_use_id")
        tool_name = None
        if tool_names is not None and tool_use_id:
            tool_name = tool_names.get(str(tool_use_id))
        out.append(
            {
                "type": "tool_result",
                "toolUseId": tool_use_id,
                "name": tool_name,
                "output": result.get("output", ""),
                "isError": bool(result.get("is_error", False)),
            }
        )
    elif event.get("type") == "result":
        out.append(
            {
                "type": "result",
                "subtype": event.get("subtype"),
                "cost": event.get("total_cost_usd"),
                "duration": event.get("duration_ms"),
                "content": event.get("subtype"),
            }
        )
    elif event.get("type") == "system" and event.get("subtype") == "init":
        out.append({"type": "session", "sessionId": event.get("session_id")})
    elif event.get("type") == "system" and event.get("subtype") == "permission_request":
        out.append({"type": "permission_request", "permission": event.get("permission")})
    return out


async def run_planning_phase(
    prompt: str,
    session: AgentSession,
    model_config: ModelConfig | None = None,
    language: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    with start_span("agent.run_planning_phase", {"session.id": session.id}) as span:
        resolved_model_config = await _resolve_model_config(model_config)
        if not resolved_model_config or not resolved_model_config.apiKey:
            yield {"type": "error", "message": "__MODEL_NOT_CONFIGURED__"}
            yield {"type": "done"}
            return

        session_cwd = Path(_get_session_work_dir(str(WORK_DIR), prompt, None)).expanduser().resolve()
        session_cwd.mkdir(parents=True, exist_ok=True)
        planning_prompt = (
            _get_workspace_instruction(str(session_cwd), False)
            + PLANNING_INSTRUCTION
            + _build_language_instruction(language, prompt)
            + prompt
        )

        options = _build_agent_options(
            resolved_model_config,
            str(session_cwd),
            session.id,
            allowed_tools=[],
        )

        try:
            agent = create_agent(options)
        except Exception as exc:
            logger.exception(
                "failed to create planning agent session_id=%s model=%s base_url=%s",
                session.id,
                resolved_model_config.model,
                resolved_model_config.baseUrl,
            )
            yield {"type": "error", "message": _sanitize_error(str(exc), resolved_model_config, api_key_missing=True)}
            yield {"type": "done"}
            return

        full_response = ""
        try:
            async for sdk_event in agent.query(planning_prompt):
                if await _is_session_aborted(session):
                    yield {"type": "error", "message": "Execution aborted"}
                    yield {"type": "done"}
                    return

                if sdk_event.get("type") == "system" and sdk_event.get("subtype") == "init":
                    yield {"type": "session", "sessionId": sdk_event.get("session_id")}
                    continue
                if sdk_event.get("type") != "assistant":
                    continue

                for block in sdk_event.get("message", {}).get("content", []):
                    if block.get("type") == "text":
                        text = str(block.get("text") or "")
                        if text:
                            full_response += text
                            yield {"type": "text", "content": text}
        except Exception as exc:
            logger.exception(
                "planning query failed session_id=%s model=%s base_url=%s",
                session.id,
                resolved_model_config.model,
                resolved_model_config.baseUrl,
            )
            message = _sanitize_error(str(exc), resolved_model_config)
            yield {"type": "error", "message": message}
            yield {"type": "done"}
            return
        finally:
            await agent.close()

        parsed = _parse_planning_response(full_response)
        task_like_prompt = _looks_like_task_request(prompt)
        if parsed and parsed.get("type") == "direct_answer":
            answer = str(parsed.get("answer") or "").strip()
            if answer and not task_like_prompt:
                add_span_event(span, "planning.direct_answer")
                yield {"type": "direct_answer", "content": answer}
                yield {"type": "done"}
                return
            if answer and task_like_prompt:
                add_span_event(span, "planning.direct_answer_ignored_for_task")

        if parsed and parsed.get("type") == "plan" and isinstance(parsed.get("plan"), dict):
            plan = parsed["plan"]
            await save_plan_async(plan)
            add_span_event(span, "planning.plan_generated", {"plan.id": str(plan.get("id") or "")})
            yield {"type": "plan", "plan": TaskPlan.model_validate(plan).model_dump()}
            yield {"type": "done"}
            return

        fallback_plan = _parse_plan_from_response(full_response)
        if fallback_plan:
            await save_plan_async(fallback_plan)
            add_span_event(span, "planning.plan_generated", {"plan.id": str(fallback_plan.get("id") or "")})
            yield {"type": "plan", "plan": TaskPlan.model_validate(fallback_plan).model_dump()}
            yield {"type": "done"}
            return

        if task_like_prompt:
            auto_plan = _build_fallback_plan_from_prompt(prompt)
            await save_plan_async(auto_plan)
            add_span_event(span, "planning.plan_autogenerated", {"plan.id": str(auto_plan.get("id") or "")})
            yield {"type": "plan", "plan": TaskPlan.model_validate(auto_plan).model_dump()}
            yield {"type": "done"}
            return

        text = full_response.strip()
        if text:
            add_span_event(span, "planning.direct_answer_fallback")
            yield {"type": "direct_answer", "content": text}
        else:
            yield {"type": "error", "message": "__PLANNING_PARSE_ERROR__"}
        yield {"type": "done"}


async def run_agent(
    prompt: str,
    session: AgentSession,
    conversation: list[dict[str, Any]] | None = None,
    work_dir: str | None = None,
    task_id: str | None = None,
    model_config: ModelConfig | None = None,
    sandbox_config: dict[str, Any] | None = None,
    images: list[dict[str, Any]] | None = None,
    skills_config: dict[str, Any] | None = None,
    mcp_config: dict[str, Any] | None = None,
    language: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    with start_span("agent.run_agent", {"session.id": session.id, "task.id": task_id or ""}) as span:
        resolved_model_config = await _resolve_model_config(model_config)
        if not resolved_model_config or not resolved_model_config.apiKey:
            yield {"type": "error", "message": "__MODEL_NOT_CONFIGURED__"}
            yield {"type": "done"}
            return

        session_cwd = Path(_get_session_work_dir(work_dir, prompt, task_id)).expanduser().resolve()
        session_cwd.mkdir(parents=True, exist_ok=True)

        skills_paths = _resolve_skills_paths(skills_config)
        mcp_servers = _load_mcp_servers(mcp_config)
        workspace_instruction = _get_workspace_instruction(str(session_cwd), bool(sandbox_config and sandbox_config.get("enabled")))
        conversation_context = _format_conversation_history(conversation)
        language_instruction = _build_language_instruction(language, prompt)

        image_paths = _save_images_to_workdir(images, session_cwd)
        image_instruction = ""
        if image_paths:
            image_instruction = (
                "## MANDATORY IMAGE ANALYSIS - DO THIS FIRST\n"
                f"The user attached {len(image_paths)} image file(s):\n"
                + "\n".join(f"{idx + 1}. {p}" for idx, p in enumerate(image_paths))
                + "\nUse Read tool on the image paths before finalizing your answer.\n\n"
            )

        enhanced_prompt = (
            image_instruction
            + workspace_instruction
            + conversation_context
            + language_instruction
            + prompt
        )

        async def _on_permission_request(permission: dict[str, Any]) -> None:
            await _register_permission_request(session.id, permission)

        async def _wait_permission(permission_id: str) -> bool:
            return await _wait_for_permission_decision(session.id, permission_id)

        options = _build_agent_options(
            resolved_model_config,
            str(session_cwd),
            session.id,
            sandbox_config=sandbox_config,
            skills_paths=skills_paths,
            mcp_servers=mcp_servers,
            on_permission_request=_on_permission_request,
            wait_for_permission_decision=_wait_permission,
        )

        try:
            agent = create_agent(options)
        except Exception as exc:
            logger.exception(
                "failed to create execution agent session_id=%s task_id=%s model=%s base_url=%s",
                session.id,
                task_id or "",
                resolved_model_config.model,
                resolved_model_config.baseUrl,
            )
            message = _sanitize_error(str(exc), resolved_model_config, api_key_missing=True)
            yield {"type": "error", "message": message}
            yield {"type": "done"}
            return

        try:
            tool_names: dict[str, str] = {}
            observed_tool_names: set[str] = set()
            assistant_text_chunks: list[str] = []
            async for sdk_event in agent.query(enhanced_prompt):
                if await _is_session_aborted(session):
                    yield {"type": "error", "message": "Execution aborted"}
                    yield {"type": "done"}
                    return
                mapped = await _map_sdk_event(sdk_event, tool_names)
                for event in mapped:
                    if event.get("type") == "tool_use":
                        observed_tool_names.add(str(event.get("name") or "").strip())
                        add_span_event(span, "tool.use", {"tool.name": str(event.get("name") or "unknown")})
                    if event.get("type") == "tool_result":
                        add_span_event(
                            span,
                            "tool.result",
                            {
                                "tool.name": str(event.get("name") or "unknown"),
                                "tool.is_error": bool(event.get("isError")),
                            },
                        )
                    if event.get("type") == "text":
                        assistant_text_chunks.append(str(event.get("content") or ""))
                    if event.get("type") == "result" and str(event.get("subtype") or "") == "success":
                        combined_text = "".join(assistant_text_chunks)
                        if _should_block_unverified_file_success(prompt, observed_tool_names, combined_text):
                            add_span_event(
                                span,
                                "result.blocked_unverified_file_success",
                                {
                                    "task.id": task_id or "",
                                    "tools": ",".join(sorted(t for t in observed_tool_names if t)),
                                },
                            )
                            lang = _resolve_language(language, prompt)
                            message = (
                                "未检测到真实的文件写入/验证工具调用，已阻止“已生成”结果。请重新执行任务。"
                                if lang == "zh-CN"
                                else "Blocked unverifiable file-success response because no file write/verify tool call was detected. Please run the task again."
                            )
                            yield {"type": "error", "message": "__UNVERIFIED_FILE_OPERATION__"}
                            yield {"type": "text", "content": message}
                            yield {**event, "subtype": "error", "content": "error"}
                            continue
                    yield event
        except Exception as exc:
            logger.exception(
                "agent query failed session_id=%s task_id=%s model=%s base_url=%s",
                session.id,
                task_id or "",
                resolved_model_config.model,
                resolved_model_config.baseUrl,
            )
            yield {"type": "error", "message": _sanitize_error(str(exc), resolved_model_config)}
        finally:
            await agent.close()

        yield {"type": "done"}


def _format_plan_for_execution(
    plan: dict[str, Any],
    work_dir: str,
    *,
    sandbox_enabled: bool,
    language: str | None,
    original_prompt: str,
) -> str:
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    steps_text = "\n".join(
        f"{idx + 1}. {str(step.get('description') if isinstance(step, dict) else step)}"
        for idx, step in enumerate(steps)
    )
    notes = str(plan.get("notes") or "")
    return (
        "You are executing a pre-approved plan. Follow these steps in order:\n"
        + _get_workspace_instruction(work_dir, sandbox_enabled)
        + _build_language_instruction(language, original_prompt)
        + f"\nGoal: {plan.get('goal', '')}\n\nSteps:\n{steps_text}\n"
        + (f"\nNotes: {notes}\n" if notes else "\n")
        + "\nNow execute this plan. You have full permissions to use all available tools.\n"
        + "CRITICAL EXECUTION RULES:\n"
        + "- Do not ask for additional confirmation; the plan is already approved.\n"
        + "- Perform real tool calls for file/system actions.\n"
        + "- Do not claim completion without tool-backed evidence.\n"
        + "- If a target file is missing and creation is required, create it directly.\n"
        + "- In the final response, include absolute paths of created/updated files.\n\nOriginal request: "
        + original_prompt
    )


async def run_execution_phase(
    plan_id: str,
    session: AgentSession,
    original_prompt: str,
    work_dir: str | None = None,
    task_id: str | None = None,
    model_config: ModelConfig | None = None,
    sandbox_config: dict[str, Any] | None = None,
    skills_config: dict[str, Any] | None = None,
    mcp_config: dict[str, Any] | None = None,
    language: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    with start_span("agent.run_execution_phase", {"session.id": session.id, "plan.id": plan_id}) as span:
        plan = await get_plan_async(plan_id)
        if not plan:
            yield {"type": "error", "message": f"Plan not found: {plan_id}"}
            yield {"type": "done"}
            return

        session_cwd = _get_session_work_dir(work_dir, original_prompt, task_id)
        execution_prompt = _format_plan_for_execution(
            plan,
            session_cwd,
            sandbox_enabled=bool(sandbox_config and sandbox_config.get("enabled")),
            language=language,
            original_prompt=original_prompt,
        )

        try:
            async for event in run_agent(
                execution_prompt,
                session,
                work_dir=session_cwd,
                task_id=task_id,
                model_config=model_config,
                sandbox_config=sandbox_config,
                skills_config=skills_config,
                mcp_config=mcp_config,
                language=language,
            ):
                yield event
        finally:
            add_span_event(span, "execution.plan_deleted", {"plan.id": plan_id})
            await delete_plan_async(plan_id)


# -----------------------------------------------------------------------------
# Upstream-style camelCase compatibility aliases
# -----------------------------------------------------------------------------


def createSession(phase: str = "plan") -> AgentSession:
    return create_session(phase)


def getSession(session_id: str) -> AgentSession | None:
    return get_session(session_id)


def deleteSession(session_id: str) -> bool:
    return delete_session(session_id)


def getPlan(plan_id: str) -> dict[str, Any] | None:
    return get_plan(plan_id)


def savePlan(plan: dict[str, Any]) -> None:
    save_plan(plan)


def deletePlan(plan_id: str) -> bool:
    return delete_plan(plan_id)


def stopAgent(session_id: str) -> None:
    stop_agent(session_id)


async def runPlanningPhase(
    prompt: str,
    session: AgentSession,
    modelConfig: ModelConfig | None = None,
    language: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    async for event in run_planning_phase(
        prompt,
        session,
        model_config=modelConfig,
        language=language,
    ):
        yield event


async def runExecutionPhase(
    planId: str,
    session: AgentSession,
    originalPrompt: str,
    workDir: str | None = None,
    taskId: str | None = None,
    modelConfig: ModelConfig | None = None,
    sandboxConfig: dict[str, Any] | None = None,
    skillsConfig: dict[str, Any] | None = None,
    mcpConfig: dict[str, Any] | None = None,
    language: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    async for event in run_execution_phase(
        planId,
        session,
        original_prompt=originalPrompt,
        work_dir=workDir,
        task_id=taskId,
        model_config=modelConfig,
        sandbox_config=sandboxConfig,
        skills_config=skillsConfig,
        mcp_config=mcpConfig,
        language=language,
    ):
        yield event


async def runAgent(
    prompt: str,
    session: AgentSession,
    conversation: list[dict[str, Any]] | None = None,
    workDir: str | None = None,
    taskId: str | None = None,
    modelConfig: ModelConfig | None = None,
    sandboxConfig: dict[str, Any] | None = None,
    images: list[dict[str, Any]] | None = None,
    skillsConfig: dict[str, Any] | None = None,
    mcpConfig: dict[str, Any] | None = None,
    language: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    async for event in run_agent(
        prompt,
        session,
        conversation=conversation,
        work_dir=workDir,
        task_id=taskId,
        model_config=modelConfig,
        sandbox_config=sandboxConfig,
        images=images,
        skills_config=skillsConfig,
        mcp_config=mcpConfig,
        language=language,
    ):
        yield event


