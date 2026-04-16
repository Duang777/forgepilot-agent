from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

_cached_git_status: str | None = None
_cached_git_status_cwd: str | None = None


def _run_git(cwd: str, command: str, timeout_s: int = 5) -> str | None:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        if result.returncode != 0:
            return None
        text = result.stdout.strip()
        return text or None
    except Exception:
        return None


async def get_git_status(cwd: str) -> str:
    global _cached_git_status, _cached_git_status_cwd
    if _cached_git_status and _cached_git_status_cwd == cwd:
        return _cached_git_status

    def _collect() -> str:
        if not _run_git(cwd, "git rev-parse --git-dir"):
            return ""
        parts: list[str] = []
        branch = _run_git(cwd, "git rev-parse --abbrev-ref HEAD")
        if branch:
            parts.append(f"Current branch: {branch}")

        main_branch = _detect_main_branch(cwd)
        if main_branch:
            parts.append(f"Main branch: {main_branch}")

        user = _run_git(cwd, "git config user.name", timeout_s=3)
        if user:
            parts.append(f"Git user: {user}")

        status = _run_git(cwd, "git status --short")
        if status:
            suffix = "\n...(truncated)" if len(status) > 2000 else ""
            parts.append(f"Status:\n{status[:2000]}{suffix}")

        if _run_git(cwd, "git rev-parse HEAD"):
            log = _run_git(cwd, "git log --oneline -5 --no-decorate")
            if log:
                parts.append(f"Recent commits:\n{log}")
        return "\n\n".join(parts)

    text = await asyncio.to_thread(_collect)
    _cached_git_status = text
    _cached_git_status_cwd = cwd
    return text


def _detect_main_branch(cwd: str) -> str | None:
    branches = _run_git(cwd, "git branch -l main master", timeout_s=3) or ""
    if "main" in branches:
        return "main"
    if "master" in branches:
        return "master"
    return None


async def discover_project_context_files(cwd: str) -> list[str]:
    base = Path(cwd)
    candidates = [
        base / "AGENT.md",
        base / "CLAUDE.md",
        base / ".claude" / "CLAUDE.md",
        base / "claude.md",
    ]
    home = Path(os.path.expanduser("~"))
    candidates.append(home / ".claude" / "CLAUDE.md")

    found: list[str] = []
    for path in candidates:
        try:
            if path.is_file():
                found.append(str(path))
        except Exception:
            continue
    return found


async def read_project_context_content(cwd: str) -> str:
    files = await discover_project_context_files(cwd)
    if not files:
        return ""
    parts: list[str] = []
    for file_path in files:
        try:
            content = Path(file_path).read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if content:
            parts.append(f"# From {file_path}:\n{content}")
    return "\n\n".join(parts)


async def get_system_context(cwd: str) -> str:
    git_status = await get_git_status(cwd)
    return f"gitStatus: {git_status}" if git_status else ""


async def get_user_context(cwd: str) -> str:
    parts: list[str] = [f"# currentDate\nToday's date is {__import__('datetime').date.today().isoformat()}."]
    project_ctx = await read_project_context_content(cwd)
    if project_ctx:
        parts.append(project_ctx)
    return "\n\n".join(parts)


def clear_context_cache() -> None:
    global _cached_git_status, _cached_git_status_cwd
    _cached_git_status = None
    _cached_git_status_cwd = None


async def getGitStatus(cwd: str) -> str:
    return await get_git_status(cwd)


async def discoverProjectContextFiles(cwd: str) -> list[str]:
    return await discover_project_context_files(cwd)


async def readProjectContextContent(cwd: str) -> str:
    return await read_project_context_content(cwd)


async def getSystemContext(cwd: str) -> str:
    return await get_system_context(cwd)


async def getUserContext(cwd: str) -> str:
    return await get_user_context(cwd)


def clearContextCache() -> None:
    clear_context_cache()
