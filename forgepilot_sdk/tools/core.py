from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import httpx

from forgepilot_sdk.tools.base import define_tool
from forgepilot_sdk.types import ToolContext, ToolResult

_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"}
_SYMBOL_PATTERN = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b")

# ============================================================================
# Global state stores (parity with TypeScript module-level stores)
# ============================================================================

_registered_agents: dict[str, dict[str, Any]] = {}
_deferred_tools: list[Any] = []
_question_handler: Callable[[str, list[str] | None], Awaitable[str]] | None = None
_mcp_connections: list[Any] = []

_task_store: dict[str, dict[str, Any]] = {}
_task_counter = 0

_team_store: dict[str, dict[str, Any]] = {}
_team_counter = 0

_mailboxes: dict[str, list[dict[str, Any]]] = {}

_plan_mode_active = False
_current_plan: str | None = None

_cron_store: dict[str, dict[str, Any]] = {}
_cron_counter = 0

_config_store: dict[str, Any] = {}
_todo_list: list[dict[str, Any]] = []
_todo_counter = 0

_active_worktrees: dict[str, dict[str, str]] = {}

_BUILTIN_AGENTS: dict[str, dict[str, Any]] = {
    "Explore": {
        "description": "Fast agent for exploring codebases.",
        "prompt": "You are a codebase exploration agent. Use tools to search files and answer questions precisely.",
        "tools": ["Read", "Glob", "Grep", "Bash"],
        "maxTurns": 10,
    },
    "Plan": {
        "description": "Software architect agent for implementation planning.",
        "prompt": "You are a software architect. Design practical implementation plans and identify key files.",
        "tools": ["Read", "Glob", "Grep", "Bash"],
        "maxTurns": 10,
    },
}


# ============================================================================
# Public state helpers (parity exports)
# ============================================================================


def register_agents(agents: dict[str, dict[str, Any]]) -> None:
    _registered_agents.update(agents)


def clear_agents() -> None:
    _registered_agents.clear()


def set_question_handler(handler: Callable[[str, list[str] | None], Awaitable[str]]) -> None:
    global _question_handler
    _question_handler = handler


def clear_question_handler() -> None:
    global _question_handler
    _question_handler = None


def set_deferred_tools(tools: list[Any]) -> None:
    global _deferred_tools
    _deferred_tools = list(tools)


def set_mcp_connections(connections: list[Any]) -> None:
    global _mcp_connections
    _mcp_connections = list(connections)


def get_all_tasks() -> list[dict[str, Any]]:
    return list(_task_store.values())


def get_task(task_id: str) -> dict[str, Any] | None:
    return _task_store.get(task_id)


def clear_tasks() -> None:
    global _task_counter
    _task_store.clear()
    _task_counter = 0


def get_all_teams() -> list[dict[str, Any]]:
    return list(_team_store.values())


def get_team(team_id: str) -> dict[str, Any] | None:
    return _team_store.get(team_id)


def clear_teams() -> None:
    global _team_counter
    _team_store.clear()
    _team_counter = 0


def read_mailbox(agent_name: str) -> list[dict[str, Any]]:
    messages = list(_mailboxes.get(agent_name, []))
    _mailboxes[agent_name] = []
    return messages


def write_to_mailbox(agent_name: str, message: dict[str, Any]) -> None:
    _mailboxes.setdefault(agent_name, []).append(message)


def clear_mailboxes() -> None:
    _mailboxes.clear()


def is_plan_mode_active() -> bool:
    return _plan_mode_active


def get_current_plan() -> str | None:
    return _current_plan


def get_all_cron_jobs() -> list[dict[str, Any]]:
    return list(_cron_store.values())


def clear_cron_jobs() -> None:
    global _cron_counter
    _cron_store.clear()
    _cron_counter = 0


def get_config(key: str) -> Any:
    return _config_store.get(key)


def set_config(key: str, value: Any) -> None:
    _config_store[key] = value


def clear_config() -> None:
    _config_store.clear()


def get_todos() -> list[dict[str, Any]]:
    return list(_todo_list)


def clear_todos() -> None:
    global _todo_counter
    _todo_list.clear()
    _todo_counter = 0


# ============================================================================
# Shared helpers
# ============================================================================


def _resolve_path(cwd: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _line_numbered(lines: list[str], offset: int) -> str:
    return "\n".join(f"{offset + idx + 1}\t{line}" for idx, line in enumerate(lines))


def _extract_text_from_mcp_result(result: Any) -> tuple[str, bool]:
    if isinstance(result, dict):
        is_error = bool(result.get("isError") or result.get("is_error"))
        content = result.get("content")
        if isinstance(content, list):
            chunks: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    chunks.append(str(block.get("text", "")))
                else:
                    chunks.append(_to_json(block))
            return "\n".join([c for c in chunks if c]).strip() or _to_json(result), is_error
        if content is not None:
            return str(content), is_error
    if isinstance(result, list):
        return "\n".join(str(item) for item in result), False
    return str(result), False


def _iter_files(base: Path) -> list[Path]:
    if base.is_file():
        return [base]
    files: list[Path] = []
    for path in base.rglob("*"):
        if path.is_file():
            files.append(path)
    return files


def _is_probably_text(path: Path) -> bool:
    try:
        sample = path.read_bytes()[:4096]
    except Exception:
        return False
    return b"\x00" not in sample


def _split_lines(text: str) -> list[str]:
    return text.split("\n")

# ============================================================================
# Core file / shell / web tools
# ============================================================================


async def _read_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    file_path = input_data.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return ToolResult(content="file_path is required", is_error=True)

    try:
        path = _resolve_path(ctx.cwd, file_path)
        if not path.exists():
            return ToolResult(content=f"Error: File not found: {path}", is_error=True)
        if path.is_dir():
            return ToolResult(
                content=f"Error: {path} is a directory, not a file. Use Bash with 'ls' to list directory contents.",
                is_error=True,
            )

        ext = path.suffix.lower().lstrip(".")
        if ext in _IMAGE_EXTENSIONS:
            return ToolResult(content=f"[Image file: {path} ({path.stat().st_size} bytes)]")

        content = path.read_text(encoding="utf-8", errors="replace")
        lines = _split_lines(content)
        offset = int(input_data.get("offset", 0) or 0)
        limit = int(input_data.get("limit", 2000) or 2000)
        selected = lines[offset : offset + limit]
        output = _line_numbered(selected, offset)
        if len(lines) > offset + limit:
            output += f"\n\n({len(lines) - offset - limit} more lines not shown)"
        return ToolResult(content=output or "(empty file)")
    except Exception as exc:
        return ToolResult(content=f"Error reading file: {exc}", is_error=True)


async def _write_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    file_path = input_data.get("file_path")
    content = input_data.get("content")
    if not isinstance(file_path, str) or not file_path:
        return ToolResult(content="file_path is required", is_error=True)
    if not isinstance(content, str):
        return ToolResult(content="content is required", is_error=True)

    try:
        path = _resolve_path(ctx.cwd, file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        lines = len(content.split("\n"))
        size = len(content.encode("utf-8"))
        return ToolResult(content=f"File written: {path} ({lines} lines, {size} bytes)")
    except Exception as exc:
        return ToolResult(content=f"Error writing file: {exc}", is_error=True)


async def _edit_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    file_path = input_data.get("file_path")
    old_string = input_data.get("old_string")
    new_string = input_data.get("new_string")
    replace_all = bool(input_data.get("replace_all", False))

    if not isinstance(file_path, str) or not file_path:
        return ToolResult(content="file_path is required", is_error=True)
    if not isinstance(old_string, str) or not isinstance(new_string, str):
        return ToolResult(content="old_string and new_string are required", is_error=True)
    if old_string == new_string:
        return ToolResult(content="Error: old_string and new_string are identical", is_error=True)

    try:
        path = _resolve_path(ctx.cwd, file_path)
        source = path.read_text(encoding="utf-8", errors="replace")
        if old_string not in source:
            return ToolResult(
                content=(
                    f"Error: old_string not found in {path}. "
                    "Make sure it matches exactly including whitespace."
                ),
                is_error=True,
            )

        if replace_all:
            updated = source.replace(old_string, new_string)
        else:
            count = source.count(old_string)
            if count > 1:
                return ToolResult(
                    content=(
                        f"Error: old_string appears {count} times in the file. "
                        "Provide more context to make it unique, or set replace_all: true."
                    ),
                    is_error=True,
                )
            updated = source.replace(old_string, new_string, 1)

        path.write_text(updated, encoding="utf-8")
        return ToolResult(content=f"File edited: {path}")
    except FileNotFoundError:
        return ToolResult(content=f"Error: File not found: {file_path}", is_error=True)
    except Exception as exc:
        return ToolResult(content=f"Error editing file: {exc}", is_error=True)


async def _glob_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    pattern = str(input_data.get("pattern") or "").strip()
    if not pattern:
        return ToolResult(content="pattern is required", is_error=True)

    search_path = str(input_data.get("path") or ".")
    try:
        base = _resolve_path(ctx.cwd, search_path)
        if not base.exists():
            return ToolResult(content=f"No files matching pattern \"{pattern}\" in {base}")

        matches = [p for p in base.glob(pattern) if p.exists()]
        matches.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        lines = [str(p.relative_to(base)) if p != base else str(p.name) for p in matches[:500]]
        if not lines:
            return ToolResult(content=f"No files matching pattern \"{pattern}\" in {base}")
        return ToolResult(content="\n".join(lines))
    except Exception as exc:
        return ToolResult(content=f"Error searching for files with pattern \"{pattern}\": {exc}", is_error=True)


def _normalize_output_mode(value: Any) -> str:
    mode = str(value or "files_with_matches")
    if mode not in {"content", "files_with_matches", "count"}:
        return "files_with_matches"
    return mode


async def _grep_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    pattern = input_data.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return ToolResult(content="pattern is required", is_error=True)

    search_path = _resolve_path(ctx.cwd, str(input_data.get("path") or "."))
    glob_filter = input_data.get("glob")
    file_type = str(input_data.get("type") or "").lower().strip()
    output_mode = _normalize_output_mode(input_data.get("output_mode"))
    case_insensitive = bool(input_data.get("-i", False))
    show_line_numbers = bool(input_data.get("-n", True))
    head_limit = int(input_data.get("head_limit", 250) or 250)

    flags = re.IGNORECASE if case_insensitive else 0
    try:
        regex = re.compile(pattern, flags=flags)
    except re.error as exc:
        return ToolResult(content=f"Invalid regex pattern: {exc}", is_error=True)

    ext_filter = f".{file_type}" if file_type else None

    files = _iter_files(search_path)
    matched_files: list[str] = []
    content_lines: list[str] = []
    count_lines: list[str] = []

    for file in files:
        rel = str(file.relative_to(search_path)) if search_path.is_dir() else str(file)

        if ext_filter and file.suffix.lower() != ext_filter:
            continue
        if isinstance(glob_filter, str) and glob_filter.strip() and not fnmatch(rel, glob_filter):
            continue
        if not _is_probably_text(file):
            continue

        try:
            lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue

        line_hits: list[int] = []
        for idx, line in enumerate(lines, 1):
            if regex.search(line):
                line_hits.append(idx)

        if not line_hits:
            continue

        matched_files.append(rel)

        if output_mode == "count":
            count_lines.append(f"{rel}:{len(line_hits)}")
            continue

        if output_mode == "content":
            for idx in line_hits:
                line = lines[idx - 1]
                if show_line_numbers:
                    content_lines.append(f"{rel}:{idx}:{line}")
                else:
                    content_lines.append(f"{rel}:{line}")

    if output_mode == "files_with_matches":
        if not matched_files:
            return ToolResult(content=f"No matches found for pattern \"{pattern}\"")
        output = "\n".join(matched_files)
        lines = output.splitlines()
        if head_limit > 0 and len(lines) > head_limit:
            output = "\n".join(lines[:head_limit]) + f"\n... ({len(lines) - head_limit} more)"
        return ToolResult(content=output)

    if output_mode == "count":
        if not count_lines:
            return ToolResult(content=f"No matches found for pattern \"{pattern}\"")
        output = "\n".join(count_lines)
        lines = output.splitlines()
        if head_limit > 0 and len(lines) > head_limit:
            output = "\n".join(lines[:head_limit]) + f"\n... ({len(lines) - head_limit} more)"
        return ToolResult(content=output)

    if not content_lines:
        return ToolResult(content=f"No matches found for pattern \"{pattern}\"")

    output = "\n".join(content_lines)
    lines = output.splitlines()
    if head_limit > 0 and len(lines) > head_limit:
        output = "\n".join(lines[:head_limit]) + f"\n... ({len(lines) - head_limit} more)"
    return ToolResult(content=output)


async def _bash_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    command = input_data.get("command")
    if not isinstance(command, str) or not command.strip():
        return ToolResult(content="command is required", is_error=True)

    timeout = int(input_data.get("timeout") or input_data.get("timeout_ms") or 120000)
    timeout = max(1, min(timeout, 600000)) / 1000.0

    def _run() -> tuple[int, str, str]:
        if shutil.which("bash"):
            cmd = ["bash", "-lc", command]
        else:
            cmd = ["powershell", "-NoProfile", "-Command", command]
        done = subprocess.run(
            cmd,
            cwd=str(ctx.cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=None,
        )
        return done.returncode, done.stdout, done.stderr

    try:
        exit_code, stdout, stderr = await asyncio.to_thread(_run)
        output = ""
        if stdout:
            output += stdout
        if stderr:
            output += ("\n" if output else "") + stderr
        if exit_code != 0:
            output += f"\nExit code: {exit_code}"

        if len(output) > 100000:
            output = output[:50000] + "\n...(truncated)...\n" + output[-50000:]

        return ToolResult(content=output or "(no output)")
    except Exception as exc:
        return ToolResult(content=f"Error executing command: {exc}", is_error=True)


async def _web_fetch_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    url = input_data.get("url")
    if not isinstance(url, str) or not url.strip():
        return ToolResult(content="url is required", is_error=True)

    headers = input_data.get("headers") if isinstance(input_data.get("headers"), dict) else {}
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; AgentSDK/1.0)", **headers},
            )

        if response.status_code < 200 or response.status_code >= 300:
            return ToolResult(content=f"HTTP {response.status_code}: {response.reason_phrase}", is_error=True)

        content_type = response.headers.get("content-type", "")
        text = response.text

        if "text/html" in content_type:
            text = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
            text = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()

        if len(text) > 100000:
            text = text[:100000] + "\n...(truncated)"

        return ToolResult(content=text or "(empty response)")
    except Exception as exc:
        return ToolResult(content=f"Error fetching {url}: {exc}", is_error=True)


async def _web_search_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    query = input_data.get("query")
    if not isinstance(query, str) or not query.strip():
        return ToolResult(content="query is required", is_error=True)

    num_results = max(1, int(input_data.get("num_results") or input_data.get("limit") or 5))

    try:
        url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; AgentSDK/1.0)"})

        if response.status_code < 200 or response.status_code >= 300:
            return ToolResult(content=f"Search failed: HTTP {response.status_code}", is_error=True)

        html = response.text
        result_regex = re.compile(r'<a rel="nofollow" class="result__a" href="([^"]*)"[^>]*>([\s\S]*?)</a>', re.IGNORECASE)
        snippet_regex = re.compile(r'<a class="result__snippet"[^>]*>([\s\S]*?)</a>', re.IGNORECASE)

        links: list[dict[str, str]] = []
        for match in result_regex.finditer(html):
            href, title_html = match.groups()
            title = re.sub(r"<[^>]+>", "", title_html).strip()
            if href and title and "duckduckgo.com" not in href:
                links.append({"title": title, "url": href})

        snippets = [re.sub(r"<[^>]+>", "", m.group(1)).strip() for m in snippet_regex.finditer(html)]

        limit = min(num_results, len(links))
        if limit == 0:
            return ToolResult(content=f"No results found for \"{query}\"")

        result_lines: list[str] = []
        for idx in range(limit):
            link = links[idx]
            entry = f"{idx + 1}. {link['title']}\n   {link['url']}"
            if idx < len(snippets) and snippets[idx]:
                entry += f"\n   {snippets[idx]}"
            result_lines.append(entry)

        return ToolResult(content="\n\n".join(result_lines))
    except Exception as exc:
        return ToolResult(content=f"Search error: {exc}", is_error=True)

# ============================================================================
# Task tools
# ============================================================================


def _next_task_id() -> str:
    global _task_counter
    _task_counter += 1
    return f"task_{_task_counter}"


def _create_task(
    *,
    subject: str,
    description: str | None = None,
    owner: str | None = None,
    status: str = "pending",
    task_id: str | None = None,
) -> dict[str, Any]:
    tid = task_id or _next_task_id()
    now = _now()
    task = {
        "id": tid,
        "subject": subject,
        "description": description,
        "status": status,
        "owner": owner,
        "createdAt": now,
        "updatedAt": now,
        "output": "",
    }
    _task_store[tid] = task
    return task


async def _task_create_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    subject = str(input_data.get("subject") or "").strip()
    if not subject:
        return ToolResult(content="subject is required", is_error=True)

    status = str(input_data.get("status") or "pending")
    if status not in {"pending", "in_progress"}:
        status = "pending"

    task = _create_task(
        subject=subject,
        description=input_data.get("description"),
        owner=input_data.get("owner"),
        status=status,
    )
    return ToolResult(content=f"Task created: {task['id']} - \"{task['subject']}\" ({task['status']})")


async def _task_list_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    status = input_data.get("status")
    owner = input_data.get("owner")
    tasks = list(_task_store.values())

    if isinstance(status, str) and status:
        tasks = [t for t in tasks if t.get("status") == status]
    if isinstance(owner, str) and owner:
        tasks = [t for t in tasks if t.get("owner") == owner]

    if not tasks:
        return ToolResult(content="No tasks found.")

    lines: list[str] = []
    for task in tasks:
        owner_part = f" (owner: {task.get('owner')})" if task.get("owner") else ""
        lines.append(f"[{task['id']}] {str(task.get('status', '')).upper()} - {task.get('subject', '')}{owner_part}")
    return ToolResult(content="\n".join(lines))


async def _task_update_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    task_id = str(input_data.get("id") or "").strip()
    if not task_id:
        return ToolResult(content="id is required", is_error=True)

    task = _task_store.get(task_id)
    if not task:
        return ToolResult(content=f"Task not found: {task_id}", is_error=True)

    if "status" in input_data and input_data["status"]:
        task["status"] = str(input_data["status"])
    if "description" in input_data:
        task["description"] = input_data.get("description")
    if "owner" in input_data:
        task["owner"] = input_data.get("owner")
    if "output" in input_data:
        task["output"] = str(input_data.get("output") or "")
    task["updatedAt"] = _now()

    return ToolResult(content=f"Task updated: {task['id']} - {task['status']} - \"{task.get('subject', '')}\"")


async def _task_get_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    task_id = str(input_data.get("id") or "").strip()
    if not task_id:
        return ToolResult(content="id is required", is_error=True)

    task = _task_store.get(task_id)
    if not task:
        return ToolResult(content=f"Task not found: {task_id}", is_error=True)
    return ToolResult(content=_to_json(task))


async def _task_stop_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    task_id = str(input_data.get("id") or "").strip()
    if not task_id:
        return ToolResult(content="id is required", is_error=True)

    task = _task_store.get(task_id)
    if not task:
        return ToolResult(content=f"Task not found: {task_id}", is_error=True)

    task["status"] = "cancelled"
    task["updatedAt"] = _now()
    reason = input_data.get("reason")
    if isinstance(reason, str) and reason:
        task["output"] = f"Stopped: {reason}"

    return ToolResult(content=f"Task stopped: {task_id}")


async def _task_output_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    task_id = str(input_data.get("id") or "").strip()
    if not task_id:
        return ToolResult(content="id is required", is_error=True)

    task = _task_store.get(task_id)
    if not task:
        return ToolResult(content=f"Task not found: {task_id}", is_error=True)

    return ToolResult(content=str(task.get("output") or "(no output yet)"))


async def _task_compat_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    action = str(input_data.get("action") or "").strip().lower()

    if action == "create":
        provided_id = str(input_data.get("task_id") or "").strip() or None
        subject = str(input_data.get("subject") or input_data.get("goal") or provided_id or "task").strip()
        status = str(input_data.get("status") or "pending")
        task = _create_task(
            task_id=provided_id,
            subject=subject,
            description=input_data.get("description"),
            owner=input_data.get("owner"),
            status=status,
        )
        return ToolResult(content=_to_json(task))

    if action == "list":
        return ToolResult(content=_to_json(get_all_tasks()))

    if action in {"get", "stop", "update", "output"}:
        task_id = str(input_data.get("task_id") or "").strip()
        if not task_id or task_id not in _task_store:
            return ToolResult(content=f"task not found: {task_id}", is_error=True)

        task = _task_store[task_id]

        if action == "get":
            return ToolResult(content=_to_json(task))

        if action == "stop":
            task["status"] = "stopped"
            task["updatedAt"] = _now()
            return ToolResult(content=_to_json(task))

        if action == "update":
            if "status" in input_data:
                task["status"] = input_data.get("status")
            if "goal" in input_data and input_data.get("goal"):
                task["subject"] = str(input_data["goal"])
            if "output" in input_data and input_data.get("output"):
                task["output"] = str(input_data["output"])
            task["updatedAt"] = _now()
            return ToolResult(content=_to_json(task))

        if action == "output":
            if "output" in input_data:
                task["output"] = str(input_data.get("output") or "")
                task["updatedAt"] = _now()
                return ToolResult(content=_to_json(task))
            return ToolResult(content=str(task.get("output") or ""))

    return ToolResult(content=f"Unsupported Task action: {action}", is_error=True)


# ============================================================================
# Team / messaging / agent tools
# ============================================================================


def _next_team_id() -> str:
    global _team_counter
    _team_counter += 1
    return f"team_{_team_counter}"


async def _team_create_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    name = str(input_data.get("name") or "").strip()
    if not name:
        return ToolResult(content="name is required", is_error=True)

    team_id = _next_team_id()
    members = input_data.get("members") if isinstance(input_data.get("members"), list) else []
    team = {
        "id": team_id,
        "name": name,
        "members": [str(m) for m in members],
        "leaderId": "self",
        "createdAt": _now(),
        "status": "active",
    }
    _team_store[team_id] = team
    return ToolResult(content=f"Team created: {team_id} \"{name}\" with {len(team['members'])} members")


async def _team_delete_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    team_id = str(input_data.get("id") or "").strip()
    if not team_id:
        return ToolResult(content="id is required", is_error=True)
    team = _team_store.get(team_id)
    if not team:
        return ToolResult(content=f"Team not found: {team_id}", is_error=True)

    _team_store.pop(team_id, None)
    return ToolResult(content=f"Team disbanded: {team.get('name', team_id)}")


async def _send_message_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    to = str(input_data.get("to") or "").strip()
    content = str(input_data.get("content") or "").strip()
    if not to or not content:
        return ToolResult(content="to and content are required", is_error=True)

    message = {
        "from": "self",
        "to": to,
        "content": content,
        "timestamp": _now(),
        "type": str(input_data.get("type") or "text"),
    }

    if to == "*":
        for mailbox_name in list(_mailboxes.keys()):
            write_to_mailbox(mailbox_name, {**message, "to": mailbox_name})
        return ToolResult(content="Message broadcast to all agents")

    write_to_mailbox(to, message)
    return ToolResult(content=f"Message sent to {to}")


async def _agent_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    prompt = str(input_data.get("prompt") or "").strip()
    description = str(input_data.get("description") or "").strip()
    if not prompt or not description:
        return ToolResult(content="prompt and description are required", is_error=True)

    agent_type = str(input_data.get("subagent_type") or "general-purpose").strip()
    agent_def = _registered_agents.get(agent_type) or _BUILTIN_AGENTS.get(agent_type)

    try:
        from forgepilot_sdk.engine import QueryEngine
        from forgepilot_sdk.providers import create_provider
        from forgepilot_sdk.tools.registry import filter_tools, get_all_base_tools

        tools = get_all_base_tools()
        if isinstance(agent_def, dict) and isinstance(agent_def.get("tools"), list):
            tools = filter_tools(tools, allowed_tools=[str(t) for t in agent_def["tools"]])
        tools = [t for t in tools if t.name != "Agent"]

        system_prompt = (
            str(agent_def.get("prompt"))
            if isinstance(agent_def, dict) and agent_def.get("prompt")
            else "You are a helpful assistant. Complete the given task using available tools."
        )

        model = str(
            input_data.get("model")
            or ctx.model
            or ctx.state.get("default_model")
            or "claude-sonnet-4-20250514"
        )

        provider = ctx.provider
        if provider is None:
            api_key = str(ctx.state.get("api_key") or "").strip()
            if not api_key:
                return ToolResult(content="Subagent error: API key is required for subagent provider", is_error=True)
            api_type = str(ctx.api_type or "anthropic-messages")
            provider = create_provider(api_type, api_key=api_key, base_url=ctx.state.get("base_url"))

        engine = QueryEngine(
            provider=provider,
            model=model,
            tools=tools,
            cwd=ctx.cwd,
            max_turns=int(agent_def.get("maxTurns", 10)) if isinstance(agent_def, dict) else 10,
            system_prompt=system_prompt,
            append_system_prompt=None,
            session_id=str(uuid.uuid4()),
            skill_registry=ctx.state.get("skill_registry", {}),
            mcp_servers=ctx.state.get("mcp_servers", []),
        )

        result_text = ""
        tool_calls: list[str] = []

        async for event in engine.submit_message(prompt):
            if event.get("type") != "assistant":
                continue
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    result_text = str(block.get("text") or "")
                if block.get("type") == "tool_use":
                    tool_calls.append(str(block.get("name") or ""))

        output = result_text or "(Subagent completed with no text output)"
        if tool_calls:
            output += "\n[Tools used: " + ", ".join([name for name in tool_calls if name]) + "]"
        return ToolResult(content=output)
    except Exception as exc:
        return ToolResult(content=f"Subagent error: {exc}", is_error=True)

# ============================================================================
# Worktree / planning / ask-user / search / MCP / config / todo
# ============================================================================


async def _enter_worktree_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    branch = str(input_data.get("branch") or f"worktree-{int(datetime.now(timezone.utc).timestamp())}").strip()
    path_input = input_data.get("path")

    try:
        check = await asyncio.to_thread(
            subprocess.run,
            ["git", "rev-parse", "--git-dir"],
            cwd=str(ctx.cwd),
            capture_output=True,
            text=True,
        )
        if check.returncode != 0:
            return ToolResult(content="Error creating worktree: not inside a git repository", is_error=True)

        worktree_path = (
            _resolve_path(ctx.cwd, str(path_input))
            if isinstance(path_input, str) and path_input.strip()
            else (ctx.cwd.parent / f".worktree-{branch}").resolve()
        )

        await asyncio.to_thread(
            subprocess.run,
            ["git", "branch", branch],
            cwd=str(ctx.cwd),
            capture_output=True,
            text=True,
        )

        add = await asyncio.to_thread(
            subprocess.run,
            ["git", "worktree", "add", str(worktree_path), branch],
            cwd=str(ctx.cwd),
            capture_output=True,
            text=True,
        )
        if add.returncode != 0:
            err = (add.stderr or add.stdout or "unknown error").strip()
            return ToolResult(content=f"Error creating worktree: {err}", is_error=True)

        worktree_id = str(uuid.uuid4())
        _active_worktrees[worktree_id] = {
            "path": str(worktree_path),
            "branch": branch,
            "original_cwd": str(ctx.cwd),
        }
        ctx.cwd = worktree_path

        content = (
            "Worktree created:\n"
            f"  ID: {worktree_id}\n"
            f"  Path: {worktree_path}\n"
            f"  Branch: {branch}\n\n"
            "You are now working in the isolated worktree."
        )
        return ToolResult(content=content)
    except Exception as exc:
        return ToolResult(content=f"Error creating worktree: {exc}", is_error=True)


async def _exit_worktree_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    worktree_id = str(input_data.get("id") or "").strip()
    if not worktree_id:
        return ToolResult(content="id is required", is_error=True)

    worktree = _active_worktrees.get(worktree_id)
    if not worktree:
        return ToolResult(content=f"Worktree not found: {worktree_id}", is_error=True)

    action = str(input_data.get("action") or "remove")

    try:
        if action == "remove":
            rm = await asyncio.to_thread(
                subprocess.run,
                ["git", "worktree", "remove", worktree["path"], "--force"],
                cwd=worktree["original_cwd"],
                capture_output=True,
                text=True,
            )
            if rm.returncode != 0:
                err = (rm.stderr or rm.stdout or "unknown error").strip()
                return ToolResult(content=f"Error: {err}", is_error=True)

            await asyncio.to_thread(
                subprocess.run,
                ["git", "branch", "-D", worktree["branch"]],
                cwd=worktree["original_cwd"],
                capture_output=True,
                text=True,
            )

        if str(ctx.cwd) == worktree["path"]:
            ctx.cwd = Path(worktree["original_cwd"]).resolve()

        _active_worktrees.pop(worktree_id, None)
        return ToolResult(content=f"Worktree {'removed' if action == 'remove' else 'kept'}: {worktree['path']}")
    except Exception as exc:
        return ToolResult(content=f"Error: {exc}", is_error=True)


async def _enter_plan_mode_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del input_data, ctx
    global _plan_mode_active, _current_plan
    if _plan_mode_active:
        return ToolResult(content="Already in plan mode.")
    _plan_mode_active = True
    _current_plan = None
    return ToolResult(content="Entered plan mode. Design your approach before executing. Use ExitPlanMode when the plan is ready.")


async def _exit_plan_mode_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    global _plan_mode_active, _current_plan
    if not _plan_mode_active:
        return ToolResult(content="Not in plan mode.", is_error=True)

    _plan_mode_active = False
    _current_plan = str(input_data.get("plan")) if input_data.get("plan") else None
    status = "approved" if input_data.get("approved", True) else "pending approval"
    suffix = f"\n\nPlan:\n{_current_plan}" if _current_plan else ""
    return ToolResult(content=f"Plan mode exited. Plan status: {status}.{suffix}")


async def _ask_user_question_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    question = str(input_data.get("question") or "").strip()
    if not question:
        return ToolResult(content="question is required", is_error=True)

    options = input_data.get("options") if isinstance(input_data.get("options"), list) else None

    if _question_handler is not None:
        try:
            answer = await _question_handler(question, [str(x) for x in options] if options else None)
            return ToolResult(content=str(answer))
        except Exception as exc:
            return ToolResult(content=f"User declined to answer: {exc}", is_error=True)

    options_text = "\nOptions: " + ", ".join(str(x) for x in options) if options else ""
    return ToolResult(
        content=(
            f"[Non-interactive mode] Question: {question}{options_text}\n\n"
            "No user available to answer. Proceeding with best judgment."
        )
    )


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", tool.get("name") if isinstance(tool, dict) else ""))


def _tool_description(tool: Any) -> str:
    if hasattr(tool, "description"):
        return str(getattr(tool, "description"))
    if isinstance(tool, dict):
        return str(tool.get("description") or "")
    return ""


async def _tool_search_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    query = str(input_data.get("query") or "").strip()
    if not query:
        return ToolResult(content="query is required", is_error=True)

    max_results = int(input_data.get("max_results") or 5)
    if not _deferred_tools:
        return ToolResult(content="No deferred tools available.")

    if query.startswith("select:"):
        requested = {name.strip() for name in query[7:].split(",") if name.strip()}
        matches = [t for t in _deferred_tools if _tool_name(t) in requested]
    else:
        keywords = query.lower().split()
        matches = []
        for tool in _deferred_tools:
            text = f"{_tool_name(tool)} {_tool_description(tool)}".lower()
            if any(keyword in text for keyword in keywords):
                matches.append(tool)
                if len(matches) >= max_results:
                    break

    if not matches:
        return ToolResult(content=f"No tools found matching \"{query}\"")

    lines = [f"- {_tool_name(t)}: {_tool_description(t)[:200]}" for t in matches]
    return ToolResult(content=f"Found {len(matches)} tool(s):\n" + "\n".join(lines))


async def _list_mcp_resources_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    server = input_data.get("server")
    connections = [c for c in _mcp_connections if not server or getattr(c, "name", None) == server]

    if not connections:
        return ToolResult(content="No MCP servers connected.")

    lines: list[str] = []
    for conn in connections:
        status = str(getattr(conn, "status", "unknown"))
        if status != "connected":
            continue
        try:
            resources = await conn.list_resources()
        except Exception:
            resources = None

        if resources:
            lines.append(f"Server: {conn.name}")
            for item in resources:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("uri") or "resource"
                    description = item.get("description") or item.get("uri") or ""
                    lines.append(f"  - {name}: {description}")
                else:
                    lines.append(f"  - {item}")
        else:
            tool_count = len(getattr(conn, "tools", []) or [])
            lines.append(f"Server: {conn.name} ({tool_count} tools available)")

    return ToolResult(content="\n".join(lines) if lines else "No resources found.")


async def _read_mcp_resource_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    server = str(input_data.get("server") or "").strip()
    uri = str(input_data.get("uri") or "").strip()
    if not server or not uri:
        return ToolResult(content="server and uri are required", is_error=True)

    conn = next((c for c in _mcp_connections if getattr(c, "name", None) == server), None)
    if conn is None:
        return ToolResult(content=f"MCP server not found: {server}", is_error=True)

    try:
        result = await conn.read_resource(uri)
        if isinstance(result, dict) and isinstance(result.get("contents"), list):
            texts: list[str] = []
            for item in result["contents"]:
                if isinstance(item, dict) and "text" in item:
                    texts.append(str(item["text"]))
                else:
                    texts.append(_to_json(item))
            return ToolResult(content="\n".join(texts) if texts else "Resource read returned no content.")

        text, is_error = _extract_text_from_mcp_result(result)
        return ToolResult(content=text or "Resource read returned no content.", is_error=is_error)
    except Exception as exc:
        return ToolResult(content=f"Error reading resource: {exc}", is_error=True)


async def _config_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    action = str(input_data.get("action") or "").strip().lower()

    if action == "get":
        key = str(input_data.get("key") or "").strip()
        if not key:
            return ToolResult(content="key required for get", is_error=True)
        value = _config_store.get(key)
        if value is None:
            return ToolResult(content=f"Config key \"{key}\" not found")
        return ToolResult(content=json.dumps(value, ensure_ascii=False))

    if action == "set":
        key = str(input_data.get("key") or "").strip()
        if not key:
            return ToolResult(content="key required for set", is_error=True)
        value = input_data.get("value")
        _config_store[key] = value
        return ToolResult(content=f"Config set: {key} = {json.dumps(value, ensure_ascii=False)}")

    if action == "list":
        if not _config_store:
            return ToolResult(content="No config values set.")
        lines = [f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in _config_store.items()]
        return ToolResult(content="\n".join(lines))

    return ToolResult(content=f"Unknown action: {action}", is_error=True)


async def _todo_write_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    global _todo_counter

    # Backward compatibility with earlier implementation that wrote full todos array.
    if "todos" in input_data and not input_data.get("action"):
        todos = input_data.get("todos")
        if not isinstance(todos, list):
            return ToolResult(content="todos must be a list", is_error=True)
        _todo_list.clear()
        for idx, item in enumerate(todos, start=1):
            if isinstance(item, dict):
                _todo_list.append(
                    {
                        "id": int(item.get("id", idx)),
                        "text": str(item.get("text", "")),
                        "done": bool(item.get("done", False)),
                        "priority": item.get("priority"),
                    }
                )
            else:
                _todo_list.append({"id": idx, "text": str(item), "done": False})
        _todo_counter = max([int(t.get("id", 0)) for t in _todo_list], default=0)
        return ToolResult(content="Todo list updated")

    action = str(input_data.get("action") or "").strip().lower()

    if action == "add":
        text = str(input_data.get("text") or "").strip()
        if not text:
            return ToolResult(content="text required", is_error=True)
        _todo_counter += 1
        item = {
            "id": _todo_counter,
            "text": text,
            "done": False,
            "priority": input_data.get("priority"),
        }
        _todo_list.append(item)
        return ToolResult(content=f"Todo added: #{item['id']} \"{item['text']}\"")

    if action == "toggle":
        item_id = int(input_data.get("id") or 0)
        item = next((t for t in _todo_list if int(t.get("id", 0)) == item_id), None)
        if not item:
            return ToolResult(content=f"Todo #{item_id} not found", is_error=True)
        item["done"] = not bool(item.get("done", False))
        return ToolResult(content=f"Todo #{item_id} {'completed' if item['done'] else 'reopened'}")

    if action == "remove":
        item_id = int(input_data.get("id") or 0)
        index = next((i for i, t in enumerate(_todo_list) if int(t.get("id", 0)) == item_id), -1)
        if index == -1:
            return ToolResult(content=f"Todo #{item_id} not found", is_error=True)
        _todo_list.pop(index)
        return ToolResult(content=f"Todo #{item_id} removed")

    if action == "list":
        if not _todo_list:
            return ToolResult(content="No todos.")
        lines: list[str] = []
        for todo in _todo_list:
            mark = "x" if todo.get("done") else " "
            priority = f" ({todo.get('priority')})" if todo.get("priority") else ""
            lines.append(f"[{mark}] #{todo.get('id')} {todo.get('text')}{priority}")
        return ToolResult(content="\n".join(lines))

    if action == "clear":
        _todo_list.clear()
        return ToolResult(content="All todos cleared.")

    return ToolResult(content=f"Unknown action: {action}", is_error=True)


async def _notebook_edit_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    file_path = input_data.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return ToolResult(content="file_path is required", is_error=True)

    path = _resolve_path(ctx.cwd, file_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return ToolResult(content=f"Error: {exc}", is_error=True)

    cells = data.get("cells")
    if not isinstance(cells, list):
        return ToolResult(content="Error: Invalid notebook format", is_error=True)

    # TS-compatible command mode
    if input_data.get("command"):
        command = str(input_data.get("command"))
        cell_number = int(input_data.get("cell_number", 0))
        cell_type = str(input_data.get("cell_type") or "code")
        source = str(input_data.get("source") or "")
        source_lines = source.split("\n")

        if command == "insert":
            new_cell: dict[str, Any] = {
                "cell_type": cell_type,
                "source": [
                    line + ("\n" if idx < len(source_lines) - 1 else "")
                    for idx, line in enumerate(source_lines)
                ],
                "metadata": {},
            }
            if cell_type != "markdown":
                new_cell["outputs"] = []
                new_cell["execution_count"] = None
            cells.insert(cell_number, new_cell)
        elif command == "replace":
            if cell_number >= len(cells):
                return ToolResult(content=f"Error: Cell {cell_number} does not exist", is_error=True)
            cells[cell_number]["source"] = [
                line + ("\n" if idx < len(source_lines) - 1 else "")
                for idx, line in enumerate(source_lines)
            ]
            if input_data.get("cell_type"):
                cells[cell_number]["cell_type"] = cell_type
        elif command == "delete":
            if cell_number >= len(cells):
                return ToolResult(content=f"Error: Cell {cell_number} does not exist", is_error=True)
            cells.pop(cell_number)
        else:
            return ToolResult(content=f"Error: Unsupported command: {command}", is_error=True)

        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return ToolResult(content=f"Notebook {command}: cell {cell_number} in {path}")

    # Backward-compatible cell_index/new_source mode
    cell_index = int(input_data.get("cell_index", 0))
    new_source = str(input_data.get("new_source") or "")
    source_lines = new_source.split("\n")
    if cell_index < 0 or cell_index >= len(cells):
        return ToolResult(content="cell_index out of range", is_error=True)
    cell = cells[cell_index]
    cell["source"] = [
        line + ("\n" if idx < len(source_lines) - 1 else "")
        for idx, line in enumerate(source_lines)
    ]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return ToolResult(content=f"Notebook cell {cell_index} updated in {path}")


async def _cron_create_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    global _cron_counter

    name = str(input_data.get("name") or "").strip()
    schedule = str(input_data.get("schedule") or "").strip()
    command = str(input_data.get("command") or input_data.get("prompt") or "").strip()

    if not name or not schedule or not command:
        return ToolResult(content="name, schedule, and command are required", is_error=True)

    _cron_counter += 1
    cron_id = str(input_data.get("id") or f"cron_{_cron_counter}")
    job = {
        "id": cron_id,
        "name": name,
        "schedule": schedule,
        "command": command,
        "enabled": True,
        "createdAt": _now(),
    }
    _cron_store[cron_id] = job
    return ToolResult(content=f"Cron job created: {cron_id} \"{name}\" schedule=\"{schedule}\"")


async def _cron_delete_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    cron_id = str(input_data.get("id") or "").strip()
    if not cron_id:
        return ToolResult(content="id is required", is_error=True)
    if cron_id not in _cron_store:
        return ToolResult(content=f"Cron job not found: {cron_id}", is_error=True)
    _cron_store.pop(cron_id, None)
    return ToolResult(content=f"Cron job deleted: {cron_id}")


async def _cron_list_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del input_data, ctx
    jobs = list(_cron_store.values())
    if not jobs:
        return ToolResult(content="No cron jobs scheduled.")
    lines = [
        f"[{j['id']}] {'ENABLED' if j.get('enabled') else 'DISABLED'} \"{j['name']}\" schedule=\"{j['schedule']}\" command=\"{str(j.get('command', ''))[:50]}\""
        for j in jobs
    ]
    return ToolResult(content="\n".join(lines))


async def _remote_trigger_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    action = str(input_data.get("action") or "run").strip()
    return ToolResult(
        content=(
            f"RemoteTrigger {action}: This feature requires a connected remote backend. "
            "In standalone SDK mode, use CronCreate/CronList/CronDelete for local scheduling."
        )
    )


async def _skill_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    skill_name = str(input_data.get("skill") or "").strip().lower()
    if not skill_name:
        return ToolResult(content="Error: skill name is required", is_error=True)

    registry = ctx.state.get("skill_registry", {})
    if not isinstance(registry, dict):
        registry = {}

    skill = registry.get(skill_name)
    if not skill:
        available = ", ".join(sorted(registry.keys())) or "none"
        return ToolResult(content=f"Error: Unknown skill \"{skill_name}\". Available skills: {available}", is_error=True)

    args = str(input_data.get("args") or input_data.get("input") or "")
    prompt_text = str(skill.get("content") or "")
    if args:
        prompt_text += f"\n\n[args]\n{args}"

    result = {
        "success": True,
        "commandName": skill.get("name", skill_name),
        "status": "inline",
        "prompt": prompt_text,
    }
    return ToolResult(content=json.dumps(result, ensure_ascii=False))


def _extract_symbol_at_position(file_path: Path, line: int, character: int) -> str | None:
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None

    if line < 0 or line >= len(lines):
        return None

    line_text = lines[line]
    if character < 0:
        character = 0
    if character >= len(line_text):
        character = max(0, len(line_text) - 1)

    for match in _SYMBOL_PATTERN.finditer(line_text):
        if match.start() <= character <= match.end():
            return match.group(0)
    return None


def _search_definition_lines(base: Path, symbol: str) -> list[str]:
    pattern = re.compile(rf"\b(function|class|interface|type|const|let|var|def)\s+{re.escape(symbol)}\b")
    lines: list[str] = []
    for file in _iter_files(base):
        if not _is_probably_text(file):
            continue
        try:
            for idx, text in enumerate(file.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if pattern.search(text):
                    lines.append(f"{file}:{idx}:{text}")
                    if len(lines) >= 200:
                        return lines
        except Exception:
            continue
    return lines


def _search_symbol_lines(base: Path, symbol: str) -> list[str]:
    pattern = re.compile(re.escape(symbol))
    lines: list[str] = []
    for file in _iter_files(base):
        if not _is_probably_text(file):
            continue
        try:
            for idx, text in enumerate(file.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if pattern.search(text):
                    lines.append(f"{file}:{idx}:{text}")
                    if len(lines) >= 200:
                        return lines
        except Exception:
            continue
    return lines


async def _lsp_tool(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
    operation = str(input_data.get("operation") or "").strip()
    if not operation and input_data.get("symbol"):
        symbol = str(input_data["symbol"])
        return await _grep_tool({"pattern": rf"\b{re.escape(symbol)}\b", "path": input_data.get("path", ".")}, ctx)
    if not operation:
        return ToolResult(content="operation is required", is_error=True)

    try:
        file_path_val = input_data.get("file_path")
        line = int(input_data.get("line", 0) or 0)
        character = int(input_data.get("character", 0) or 0)
        query = str(input_data.get("query") or "").strip()

        if operation in {"goToDefinition", "goToImplementation"}:
            if not isinstance(file_path_val, str) or not file_path_val:
                return ToolResult(content="file_path and line required", is_error=True)
            file_path = _resolve_path(ctx.cwd, file_path_val)
            symbol = _extract_symbol_at_position(file_path, line, character)
            if not symbol:
                return ToolResult(content="Could not identify symbol at position")
            results = _search_definition_lines(ctx.cwd, symbol)
            return ToolResult(content="\n".join(results) if results else f"No definition found for \"{symbol}\"")

        if operation == "findReferences":
            if not isinstance(file_path_val, str) or not file_path_val:
                return ToolResult(content="file_path and line required", is_error=True)
            file_path = _resolve_path(ctx.cwd, file_path_val)
            symbol = _extract_symbol_at_position(file_path, line, character)
            if not symbol:
                return ToolResult(content="Could not identify symbol at position")
            refs = _search_symbol_lines(ctx.cwd, symbol)
            return ToolResult(content="\n".join(refs[:50]) if refs else f"No references found for \"{symbol}\"")

        if operation == "hover":
            return ToolResult(content="Hover information requires a running language server. Use Read tool to examine the file content.")

        if operation == "documentSymbol":
            if not isinstance(file_path_val, str) or not file_path_val:
                return ToolResult(content="file_path required", is_error=True)
            file_path = _resolve_path(ctx.cwd, file_path_val)
            pattern = re.compile(r"^\s*(export\s+)?(function|class|interface|type|const|let|var|enum|def)\s+")
            lines: list[str] = []
            for idx, line_text in enumerate(file_path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if pattern.search(line_text):
                    lines.append(f"{idx}:{line_text}")
            return ToolResult(content="\n".join(lines) if lines else "No symbols found")

        if operation == "workspaceSymbol":
            if not query:
                return ToolResult(content="query required", is_error=True)
            ws = _search_symbol_lines(ctx.cwd, query)
            return ToolResult(content="\n".join(ws[:30]) if ws else f"No symbols found for \"{query}\"")

        return ToolResult(content=f"LSP operation \"{operation}\" requires a running language server.")
    except Exception as exc:
        return ToolResult(content=f"LSP error: {exc}", is_error=True)

# ============================================================================
# Tool registry builder
# ============================================================================


def build_core_tools() -> list:
    tools = []

    tools.append(
        define_tool(
            name="Bash",
            description="Execute a bash command and return its output. Use for running shell commands, scripts, and system operations.",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "number"},
                },
                "required": ["command"],
            },
            call=_bash_tool,
            concurrency_safe=False,
        )
    )

    tools.append(
        define_tool(
            name="Read",
            description="Read a file from the filesystem. Returns content with line numbers.",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "offset": {"type": "number"},
                    "limit": {"type": "number"},
                },
                "required": ["file_path"],
            },
            call=_read_tool,
            read_only=True,
        )
    )

    tools.append(
        define_tool(
            name="Write",
            description="Write content to a file. Creates the file if it does not exist, or overwrites if it does.",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["file_path", "content"],
            },
            call=_write_tool,
            concurrency_safe=False,
        )
    )

    tools.append(
        define_tool(
            name="Edit",
            description="Perform exact string replacements in files.",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["file_path", "old_string", "new_string"],
            },
            call=_edit_tool,
            concurrency_safe=False,
        )
    )

    tools.append(
        define_tool(
            name="Glob",
            description="Find files matching a glob pattern.",
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["pattern"],
            },
            call=_glob_tool,
            read_only=True,
        )
    )

    tools.append(
        define_tool(
            name="Grep",
            description="Search file contents using regex patterns.",
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "glob": {"type": "string"},
                    "type": {"type": "string"},
                    "output_mode": {"type": "string", "enum": ["content", "files_with_matches", "count"]},
                    "-i": {"type": "boolean"},
                    "-n": {"type": "boolean"},
                    "head_limit": {"type": "number"},
                },
                "required": ["pattern"],
            },
            call=_grep_tool,
            read_only=True,
        )
    )

    tools.append(
        define_tool(
            name="NotebookEdit",
            description="Edit Jupyter notebook (.ipynb) cells. Can insert, replace, or delete cells.",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "command": {"type": "string", "enum": ["insert", "replace", "delete"]},
                    "cell_number": {"type": "number"},
                    "cell_type": {"type": "string", "enum": ["code", "markdown"]},
                    "source": {"type": "string"},
                    "cell_index": {"type": "number"},
                    "new_source": {"type": "string"},
                },
                "required": ["file_path"],
            },
            call=_notebook_edit_tool,
            concurrency_safe=False,
        )
    )

    tools.append(
        define_tool(
            name="WebFetch",
            description="Fetch content from a URL and return it as text.",
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string"}, "headers": {"type": "object"}},
                "required": ["url"],
            },
            call=_web_fetch_tool,
            read_only=True,
        )
    )

    tools.append(
        define_tool(
            name="WebSearch",
            description="Search the web for information.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "num_results": {"type": "number"},
                    "limit": {"type": "number"},
                },
                "required": ["query"],
            },
            call=_web_search_tool,
            read_only=True,
        )
    )
    tools.append(
        define_tool(
            name="Agent",
            description="Launch a subagent to handle complex, multi-step tasks autonomously.",
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "description": {"type": "string"},
                    "subagent_type": {"type": "string"},
                    "model": {"type": "string"},
                    "name": {"type": "string"},
                    "run_in_background": {"type": "boolean"},
                },
                "required": ["prompt", "description"],
            },
            call=_agent_tool,
            concurrency_safe=False,
        )
    )

    tools.append(
        define_tool(
            name="SendMessage",
            description="Send a message to another agent or teammate.",
            input_schema={
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "content": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["text", "shutdown_request", "shutdown_response", "plan_approval_response"],
                    },
                },
                "required": ["to", "content"],
            },
            call=_send_message_tool,
        )
    )

    tools.append(
        define_tool(
            name="TeamCreate",
            description="Create a multi-agent team for coordinated work.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "members": {"type": "array", "items": {"type": "string"}},
                    "task_description": {"type": "string"},
                },
                "required": ["name"],
            },
            call=_team_create_tool,
            concurrency_safe=False,
        )
    )

    tools.append(
        define_tool(
            name="TeamDelete",
            description="Disband a team and clean up resources.",
            input_schema={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            call=_team_delete_tool,
            concurrency_safe=False,
        )
    )

    tools.append(
        define_tool(
            name="TaskCreate",
            description="Create a new task for tracking work progress.",
            input_schema={
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "description": {"type": "string"},
                    "owner": {"type": "string"},
                    "status": {"type": "string", "enum": ["pending", "in_progress"]},
                },
                "required": ["subject"],
            },
            call=_task_create_tool,
        )
    )

    tools.append(
        define_tool(
            name="TaskList",
            description="List all tasks with their status and ownership.",
            input_schema={"type": "object", "properties": {"status": {"type": "string"}, "owner": {"type": "string"}}},
            call=_task_list_tool,
            read_only=True,
        )
    )

    tools.append(
        define_tool(
            name="TaskUpdate",
            description="Update a task's status, description, or other properties.",
            input_schema={
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "failed", "cancelled"]},
                    "description": {"type": "string"},
                    "owner": {"type": "string"},
                    "output": {"type": "string"},
                },
                "required": ["id"],
            },
            call=_task_update_tool,
        )
    )

    tools.append(
        define_tool(
            name="TaskGet",
            description="Get full details of a specific task.",
            input_schema={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            call=_task_get_tool,
            read_only=True,
        )
    )

    tools.append(
        define_tool(
            name="TaskStop",
            description="Stop/cancel a running task.",
            input_schema={
                "type": "object",
                "properties": {"id": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["id"],
            },
            call=_task_stop_tool,
        )
    )

    tools.append(
        define_tool(
            name="TaskOutput",
            description="Get the output/result of a task.",
            input_schema={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            call=_task_output_tool,
            read_only=True,
        )
    )

    tools.append(
        define_tool(
            name="Task",
            description="Task management tool (legacy compatibility: create/list/update/get/stop/output).",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "task_id": {"type": "string"},
                    "subject": {"type": "string"},
                    "goal": {"type": "string"},
                    "status": {"type": "string"},
                    "output": {"type": "string"},
                },
                "required": ["action"],
            },
            call=_task_compat_tool,
            concurrency_safe=False,
        )
    )

    tools.append(
        define_tool(
            name="EnterWorktree",
            description="Create an isolated git worktree for parallel work.",
            input_schema={"type": "object", "properties": {"branch": {"type": "string"}, "path": {"type": "string"}}},
            call=_enter_worktree_tool,
            concurrency_safe=False,
        )
    )

    tools.append(
        define_tool(
            name="ExitWorktree",
            description="Exit and optionally remove a git worktree.",
            input_schema={
                "type": "object",
                "properties": {"id": {"type": "string"}, "action": {"type": "string", "enum": ["keep", "remove"]}},
                "required": ["id"],
            },
            call=_exit_worktree_tool,
            concurrency_safe=False,
        )
    )

    tools.append(
        define_tool(
            name="EnterPlanMode",
            description="Enter plan/design mode for complex tasks.",
            input_schema={"type": "object", "properties": {}},
            call=_enter_plan_mode_tool,
            concurrency_safe=False,
        )
    )

    tools.append(
        define_tool(
            name="ExitPlanMode",
            description="Exit plan mode with a completed plan.",
            input_schema={
                "type": "object",
                "properties": {"plan": {"type": "string"}, "approved": {"type": "boolean"}},
            },
            call=_exit_plan_mode_tool,
            concurrency_safe=False,
        )
    )

    tools.append(
        define_tool(
            name="AskUserQuestion",
            description="Ask the user a question and wait for their response.",
            input_schema={
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "allow_multiselect": {"type": "boolean"},
                },
                "required": ["question"],
            },
            call=_ask_user_question_tool,
            read_only=True,
            concurrency_safe=False,
        )
    )

    tools.append(
        define_tool(
            name="ToolSearch",
            description="Search for additional tools that may be available but not yet loaded.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}, "max_results": {"type": "number"}},
                "required": ["query"],
            },
            call=_tool_search_tool,
            read_only=True,
        )
    )

    tools.append(
        define_tool(
            name="ListMcpResources",
            description="List available resources from connected MCP servers.",
            input_schema={"type": "object", "properties": {"server": {"type": "string"}}},
            call=_list_mcp_resources_tool,
            read_only=True,
        )
    )

    tools.append(
        define_tool(
            name="ReadMcpResource",
            description="Read a specific resource from an MCP server.",
            input_schema={
                "type": "object",
                "properties": {"server": {"type": "string"}, "uri": {"type": "string"}},
                "required": ["server", "uri"],
            },
            call=_read_mcp_resource_tool,
            read_only=True,
        )
    )

    tools.append(
        define_tool(
            name="CronCreate",
            description="Create a scheduled recurring task (cron job).",
            input_schema={
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "schedule": {"type": "string"},
                    "command": {"type": "string"},
                    "prompt": {"type": "string"},
                },
                "required": ["name", "schedule", "command"],
            },
            call=_cron_create_tool,
        )
    )

    tools.append(
        define_tool(
            name="CronDelete",
            description="Delete a scheduled cron job.",
            input_schema={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            call=_cron_delete_tool,
        )
    )

    tools.append(
        define_tool(
            name="CronList",
            description="List all scheduled cron jobs.",
            input_schema={"type": "object", "properties": {}},
            call=_cron_list_tool,
            read_only=True,
        )
    )

    tools.append(
        define_tool(
            name="RemoteTrigger",
            description="Manage remote scheduled agent triggers.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "get", "create", "update", "run"]},
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "schedule": {"type": "string"},
                    "prompt": {"type": "string"},
                },
                "required": ["action"],
            },
            call=_remote_trigger_tool,
        )
    )

    tools.append(
        define_tool(
            name="LSP",
            description="Language Server Protocol operations for code intelligence.",
            input_schema={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "goToDefinition",
                            "findReferences",
                            "hover",
                            "documentSymbol",
                            "workspaceSymbol",
                            "goToImplementation",
                            "prepareCallHierarchy",
                            "incomingCalls",
                            "outgoingCalls",
                        ],
                    },
                    "file_path": {"type": "string"},
                    "line": {"type": "number"},
                    "character": {"type": "number"},
                    "query": {"type": "string"},
                    "symbol": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": [],
            },
            call=_lsp_tool,
            read_only=True,
        )
    )

    tools.append(
        define_tool(
            name="Config",
            description="Get or set configuration values. Supports session-scoped settings.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["get", "set", "list"]},
                    "key": {"type": "string"},
                    "value": {},
                },
                "required": ["action"],
            },
            call=_config_tool,
        )
    )

    tools.append(
        define_tool(
            name="TodoWrite",
            description="Manage a session todo/checklist.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "toggle", "remove", "list", "clear"]},
                    "text": {"type": "string"},
                    "id": {"type": "number"},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    "todos": {
                        "type": "array",
                        "items": {
                            "anyOf": [
                                {"type": "string"},
                                {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "number"},
                                        "text": {"type": "string"},
                                        "done": {"type": "boolean"},
                                        "priority": {"type": "string"},
                                    },
                                },
                            ]
                        },
                    },
                },
                "required": [],
            },
            call=_todo_write_tool,
        )
    )

    tools.append(
        define_tool(
            name="Skill",
            description="Execute a skill within the current conversation.",
            input_schema={
                "type": "object",
                "properties": {
                    "skill": {"type": "string"},
                    "args": {"type": "string"},
                    "input": {"type": "object"},
                },
                "required": ["skill"],
            },
            call=_skill_tool,
            concurrency_safe=False,
        )
    )

    return tools
