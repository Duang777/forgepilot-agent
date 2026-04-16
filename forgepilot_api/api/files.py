from __future__ import annotations

import asyncio
import base64
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from forgepilot_api.config import get_all_skills_dirs, get_home_dir
from forgepilot_api.core.settings import get_settings
from forgepilot_api.storage.repositories import list_files_by_task

router = APIRouter(prefix="/files", tags=["files"])
_import_skill_locks: dict[str, asyncio.Lock] = {}
_DEFAULT_IMPORT_SELF_CHECK_URL = "https://github.com/geekjourneyx/md2wechat-skill/tree/main/skills/md2wechat"
_DEFAULT_IMPORT_SELF_CHECK_BRANCH = "main"
_DEFAULT_IMPORT_SELF_CHECK_PATH = "skills/md2wechat"

IGNORED_NAMES = {
    "node_modules",
    "bower_components",
    "jspm_packages",
    "vendor",
    "__pycache__",
    ".pnpm",
    "dist",
    "build",
    "out",
    ".next",
    ".nuxt",
    ".output",
    ".vercel",
    ".netlify",
    ".cache",
    ".parcel-cache",
    ".turbo",
    ".swc",
    ".eslintcache",
    ".stylelintcache",
    ".idea",
    ".vscode",
    ".vs",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "logs",
    "coverage",
    ".nyc_output",
    "tmp",
    "temp",
    ".tmp",
    ".temp",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "composer.lock",
    "Cargo.lock",
}

PERM_FILES_READDIR = "files.readdir"
PERM_FILES_STAT = "files.stat"
PERM_FILES_READ = "files.read"
PERM_FILES_SKILLS_DIR = "files.skills_dir"
PERM_FILES_READ_BINARY = "files.read_binary"
PERM_FILES_DETECT_EDITOR = "files.detect_editor"
PERM_FILES_OPEN_EDITOR = "files.open_in_editor"
PERM_FILES_OPEN = "files.open"
PERM_FILES_IMPORT_SKILL = "files.import_skill"
PERM_FILES_IMPORT_SKILL_SELF_CHECK = "files.import_skill_self_check"
PERM_FILES_TASK = "files.task"

READ_SCOPES = {
    PERM_FILES_READDIR,
    PERM_FILES_STAT,
    PERM_FILES_READ,
    PERM_FILES_SKILLS_DIR,
    PERM_FILES_READ_BINARY,
    PERM_FILES_DETECT_EDITOR,
    PERM_FILES_TASK,
}
OPEN_SCOPES = {
    PERM_FILES_OPEN_EDITOR,
    PERM_FILES_OPEN,
}
IMPORT_SCOPES = {
    PERM_FILES_IMPORT_SKILL,
    PERM_FILES_IMPORT_SKILL_SELF_CHECK,
}
ALL_FILE_SCOPES = READ_SCOPES | OPEN_SCOPES | IMPORT_SCOPES
DANGEROUS_FILE_SCOPES = OPEN_SCOPES | IMPORT_SCOPES

SCOPE_ALIASES: dict[str, set[str]] = {
    "read": READ_SCOPES,
    "files.read": READ_SCOPES,
    "open": OPEN_SCOPES,
    "files.open": OPEN_SCOPES,
    "import": IMPORT_SCOPES,
    "files.import": IMPORT_SCOPES,
}


def _expand_acl_tokens(tokens: tuple[str, ...]) -> set[str]:
    if "*" in tokens:
        return {"*"}
    out: set[str] = set()
    for token in tokens:
        lowered = token.strip().lower()
        if not lowered:
            continue
        if lowered in SCOPE_ALIASES:
            out.update(SCOPE_ALIASES[lowered])
            continue
        if lowered in ALL_FILE_SCOPES:
            out.add(lowered)
    return out


def _resolve_subject(request: Request) -> str:
    subject = getattr(request.state, "auth_subject", None)
    if subject is None:
        return "anonymous"
    text = str(subject).strip().lower()
    return text or "anonymous"


def _authorize_files_scope(request: Request, required_scope: str) -> JSONResponse | None:
    settings = get_settings()
    if required_scope in DANGEROUS_FILE_SCOPES and not settings.files_dangerous_enabled:
        return JSONResponse(
            {"error": f"__FILES_FEATURE_DISABLED__|{required_scope}"},
            status_code=403,
        )

    auth_scopes = {
        str(scope).strip().lower()
        for scope in getattr(request.state, "auth_scopes", [])
        if str(scope).strip()
    }
    if "*" in auth_scopes or required_scope in auth_scopes:
        return None

    subject = _resolve_subject(request)
    acl_tokens = settings.files_acl_subjects.get(subject, settings.files_acl_default)
    allowed_scopes = _expand_acl_tokens(acl_tokens)
    if "*" in allowed_scopes or required_scope in allowed_scopes:
        return None
    return JSONResponse(
        {"error": f"__FILES_ACL_DENIED__|{required_scope}"},
        status_code=403,
    )


def _temp_dir() -> Path:
    if os.name == "nt":
        return Path(os.getenv("TEMP") or os.getenv("TMP") or r"C:\Windows\Temp")
    return Path("/tmp")


def _normalize(path: Path) -> str:
    text = str(path.resolve())
    return text.lower() if os.name == "nt" else text


def _is_allowed_path(path: Path) -> bool:
    home = Path(get_home_dir()).expanduser()
    temp = _temp_dir()
    normalized = _normalize(path.expanduser())
    return normalized.startswith(_normalize(home)) or normalized.startswith(_normalize(temp))


def _expand_path(raw: str) -> Path:
    if raw == "~":
        return Path(get_home_dir())
    if raw.startswith("~/") or raw.startswith("~\\"):
        return Path(str(get_home_dir()) + raw[1:])
    expanded = Path(raw).expanduser()
    if os.name == "nt":
        return Path(str(expanded).replace("/", "\\"))
    return expanded


def _should_ignore(name: str) -> bool:
    if name.startswith("."):
        return True
    if name in IGNORED_NAMES:
        return True
    lower = name.lower()
    return lower.endswith(".log") or lower.endswith(".lock") or lower.startswith("npm-debug") or lower.startswith("yarn-debug") or lower.startswith("yarn-error")


def _read_dir_recursive(dir_path: Path, depth: int = 0, max_depth: int = 3) -> list[dict[str, Any]]:
    if depth > max_depth:
        return []
    out = []
    try:
        entries = list(dir_path.iterdir())
    except Exception:
        return []

    for entry in entries:
        if _should_ignore(entry.name):
            continue
        item = {"name": entry.name, "path": str(entry), "isDir": entry.is_dir()}
        if entry.is_dir() and depth < max_depth:
            item["children"] = _read_dir_recursive(entry, depth + 1, max_depth)
        out.append(item)
    out.sort(key=lambda x: (not x["isDir"], x["name"].lower()))
    return out


def _run_cmd(command: list[str] | str, *, shell: bool = False) -> None:
    subprocess.run(command, shell=shell, check=True)


def _open_with_system_default(path: Path, *, text_mode: bool = False) -> None:
    if os.name == "nt":
        target = str(path)
        # Prefer os.startfile on Windows: it avoids shell-escaping edge cases
        # (spaces, parentheses, unicode) that can make `cmd /c start` fail.
        try:
            os.startfile(target)  # type: ignore[attr-defined]
            return
        except Exception:
            escaped_path = target.replace('"', '""')
            _run_cmd(f'cmd /c start "" "{escaped_path}"', shell=True)
        return
    if platform.system() == "Darwin":
        if text_mode:
            _run_cmd(["open", "-t", str(path)])
        else:
            _run_cmd(["open", str(path)])
        return
    _run_cmd(["xdg-open", str(path)])


def _detect_editor_candidates() -> list[tuple[str, str, str]]:
    return [
        ("Cursor", "cursor", "cursor.cmd"),
        ("VS Code", "code", "code.cmd"),
        ("VS Code Insiders", "code-insiders", "code-insiders"),
        ("Sublime Text", "subl", "subl"),
        ("Atom", "atom", "atom"),
        ("WebStorm", "webstorm", "webstorm"),
        ("PyCharm", "pycharm", "pycharm"),
    ]


def _find_editor() -> tuple[str | None, str]:
    for name, command, windows_check in _detect_editor_candidates():
        check = windows_check if os.name == "nt" else command
        if shutil.which(check) or shutil.which(command):
            return command, name
    return None, "Default Editor"


def _parse_github_repo_url(raw_url: str) -> tuple[str, str | None, str | None]:
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http/https GitHub URLs are supported")
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        raise ValueError("Only github.com URLs are supported")

    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise ValueError("Invalid GitHub repository URL")

    owner = parts[0]
    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        raise ValueError("Invalid GitHub repository URL")

    branch: str | None = None
    skill_subpath: str | None = None
    if len(parts) >= 4 and parts[2] == "tree":
        branch = unquote(parts[3]).strip() or None
        if len(parts) > 4:
            skill_subpath = unquote("/".join(parts[4:])).strip() or None

    clone_url = f"https://github.com/{owner}/{repo}.git"
    return clone_url, branch, skill_subpath


def _clone_repo_to_temp(clone_url: str, branch: str | None) -> Path:
    if not shutil.which("git"):
        raise RuntimeError("git is not installed or not in PATH")

    temp_root = Path(tempfile.mkdtemp(prefix="forgepilot-skill-import-"))
    repo_dir = temp_root / "repo"
    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd.extend(["--branch", branch])
    cmd.extend([clone_url, str(repo_dir)])
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or f"git clone failed with exit code {completed.returncode}"
        raise RuntimeError(detail)
    return repo_dir


def _collect_skill_dirs(repo_dir: Path, skill_subpath: str | None) -> list[Path]:
    roots: list[Path] = []
    if skill_subpath:
        candidate = (repo_dir / skill_subpath).resolve()
        if not str(candidate).startswith(str(repo_dir.resolve())):
            raise ValueError("Invalid skill path")
        if not candidate.exists():
            raise ValueError(f"Path not found in repository: {skill_subpath}")
        roots = [candidate]
    else:
        roots = [repo_dir]

    found: list[Path] = []
    for root in roots:
        if root.is_file() and root.name.upper() == "SKILL.MD":
            found.append(root.parent)
            continue
        if root.is_dir() and (root / "SKILL.md").exists():
            found.append(root)
            continue
        if root.is_dir():
            # Discover nested skills, but avoid huge traversal.
            for md_file in root.rglob("SKILL.md"):
                parts = md_file.relative_to(root).parts
                if len(parts) > 6:
                    continue
                found.append(md_file.parent)

    deduped: list[Path] = []
    seen: set[str] = set()
    for item in found:
        key = str(item.resolve())
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def _copy_skill_dir(skill_dir: Path, target_root: Path) -> Path:
    base_name = skill_dir.name or "skill"
    safe_name = "".join(ch for ch in base_name if ch not in '<>:"/\\|?*').strip() or "skill"
    dest = target_root / safe_name
    suffix = 1
    while dest.exists():
        dest = target_root / f"{safe_name}-{suffix}"
        suffix += 1
    shutil.copytree(skill_dir, dest)
    return dest


def _get_import_lock(target_root: Path) -> asyncio.Lock:
    key = str(target_root.resolve())
    lock = _import_skill_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _import_skill_locks[key] = lock
    return lock


@router.post("/readdir")
async def readdir(request: Request, body: dict) -> dict:
    try:
        denied = _authorize_files_scope(request, PERM_FILES_READDIR)
        if denied:
            return denied
        raw = body.get("path")
        max_depth = int(body.get("maxDepth", 3))
        if not raw:
            return JSONResponse({"error": "Path is required"}, status_code=400)

        target = _expand_path(str(raw))
        if not _is_allowed_path(target):
            return JSONResponse(
                {"error": "Access denied: path must be within home directory"},
                status_code=403,
            )

        if not target.exists():
            return {"success": False, "error": "Directory does not exist", "files": []}
        if not target.is_dir():
            return JSONResponse(
                {"success": False, "error": "Path is not a directory", "files": []},
                status_code=400,
            )

        return {"success": True, "path": str(target), "files": _read_dir_recursive(target, 0, max_depth)}
    except Exception as exc:
        return JSONResponse(
            {"success": False, "error": str(exc), "files": []},
            status_code=500,
        )


@router.post("/stat")
async def stat(request: Request, body: dict) -> dict:
    try:
        denied = _authorize_files_scope(request, PERM_FILES_STAT)
        if denied:
            return denied
        raw = body.get("path")
        if not raw:
            return JSONResponse({"error": "Path is required"}, status_code=400)
        target = _expand_path(str(raw))
        try:
            info = target.stat()
            return {
                "exists": True,
                "isFile": target.is_file(),
                "isDirectory": target.is_dir(),
                "size": info.st_size,
                "mtime": __import__("datetime").datetime.fromtimestamp(info.st_mtime).isoformat(),
            }
        except Exception:
            return {"exists": False}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/read")
async def read_file(request: Request, body: dict) -> dict:
    try:
        denied = _authorize_files_scope(request, PERM_FILES_READ)
        if denied:
            return denied
        raw = body.get("path")
        if not raw:
            return JSONResponse({"error": "Path is required"}, status_code=400)
        target = _expand_path(str(raw))
        if not _is_allowed_path(target):
            return JSONResponse({"error": "Access denied"}, status_code=403)
        text = target.read_text(encoding="utf-8", errors="replace")
        return {"success": True, "content": text}
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.get("/skills-dir")
async def skills_dir(request: Request) -> dict:
    denied = _authorize_files_scope(request, PERM_FILES_SKILLS_DIR)
    if denied:
        return denied
    directories = []
    for entry in get_all_skills_dirs():
        p = Path(entry["path"])
        exists = p.exists() and p.is_dir()
        if not exists and entry["name"] == "forgepilot":
            p.mkdir(parents=True, exist_ok=True)
            exists = True
        directories.append({"name": entry["name"], "path": str(p), "exists": exists})
    first = next((d for d in directories if d["exists"]), None)
    return {"path": first["path"] if first else "", "exists": bool(first), "directories": directories}


@router.post("/read-binary")
async def read_binary(request: Request, body: dict) -> dict:
    try:
        denied = _authorize_files_scope(request, PERM_FILES_READ_BINARY)
        if denied:
            return denied
        raw = body.get("path")
        if not raw:
            return JSONResponse({"error": "Path is required"}, status_code=400)
        target = _expand_path(str(raw))
        if not _is_allowed_path(target):
            return JSONResponse({"error": "Access denied"}, status_code=403)
        if not target.exists():
            return JSONResponse({"error": "File does not exist"}, status_code=404)
        if not target.is_file():
            return JSONResponse({"error": "Path is not a file"}, status_code=400)

        content = target.read_bytes()
        return {
            "success": True,
            "fileName": target.name,
            "content": base64.b64encode(content).decode("ascii"),
            "size": len(content),
        }
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.get("/detect-editor")
async def detect_editor(request: Request) -> dict:
    denied = _authorize_files_scope(request, PERM_FILES_DETECT_EDITOR)
    if denied:
        return denied
    command, name = _find_editor()
    if command:
        return {"success": True, "editor": name, "command": command}
    return {"success": True, "editor": "Default Editor", "command": None}


@router.post("/open-in-editor")
async def open_in_editor(request: Request, body: dict) -> dict:
    try:
        denied = _authorize_files_scope(request, PERM_FILES_OPEN_EDITOR)
        if denied:
            return denied
        raw = body.get("path")
        if not raw:
            return JSONResponse({"error": "Path is required"}, status_code=400)
        target = _expand_path(str(raw))
        if not _is_allowed_path(target):
            return JSONResponse({"error": "Access denied"}, status_code=403)
        if not target.exists():
            return JSONResponse({"error": "File does not exist"}, status_code=404)

        editor_command, editor_name = _find_editor()
        if editor_command:
            _run_cmd([editor_command, str(target)])
        else:
            _open_with_system_default(target, text_mode=True)
        return {"success": True, "editor": editor_name}
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.post("/open")
async def open_file(request: Request, body: dict) -> dict:
    try:
        denied = _authorize_files_scope(request, PERM_FILES_OPEN)
        if denied:
            return denied
        raw = body.get("path")
        if not raw:
            return JSONResponse({"error": "Path is required"}, status_code=400)

        target = _expand_path(str(raw))
        if not _is_allowed_path(target):
            return JSONResponse(
                {"error": "Access denied: path must be within home directory"},
                status_code=403,
            )
        if not target.exists():
            return JSONResponse({"error": "File does not exist"}, status_code=404)

        is_directory = target.is_dir()
        if os.name == "nt" and is_directory:
            _run_cmd(["explorer", str(target)])
        else:
            _open_with_system_default(target)
        return {"success": True}
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.post("/import-skill")
async def import_skill(request: Request, body: dict) -> dict:
    denied = _authorize_files_scope(request, PERM_FILES_IMPORT_SKILL)
    if denied:
        return denied
    url = str(body.get("url") or "").strip()
    target_dir = str(body.get("targetDir") or "").strip()
    branch_override = str(body.get("branch") or "").strip() or None
    path_override = str(body.get("path") or "").strip() or None
    if not url:
        return JSONResponse({"success": False, "error": "url is required"}, status_code=400)
    if not target_dir:
        return JSONResponse({"success": False, "error": "targetDir is required"}, status_code=400)

    target_root = _expand_path(target_dir)
    if not _is_allowed_path(target_root):
        return JSONResponse({"success": False, "error": "Access denied"}, status_code=403)

    clone_root: Path | None = None
    import_lock = _get_import_lock(target_root)
    try:
        clone_url, branch, skill_subpath = _parse_github_repo_url(url)
        effective_branch = branch_override or branch
        effective_path = path_override or skill_subpath
        clone_root = await asyncio.to_thread(_clone_repo_to_temp, clone_url, effective_branch)
        skill_dirs = _collect_skill_dirs(clone_root, effective_path)
        if not skill_dirs:
            return JSONResponse(
                {"success": False, "error": "No SKILL.md found in repository"},
                status_code=400,
            )

        async with import_lock:
            target_root.mkdir(parents=True, exist_ok=True)
            imported: list[dict[str, str]] = []
            for skill_dir in skill_dirs:
                dest = await asyncio.to_thread(_copy_skill_dir, skill_dir, target_root)
                imported.append({"name": dest.name, "path": str(dest)})

        return {
            "success": True,
            "count": len(imported),
            "targetDir": str(target_root),
            "source": {
                "url": url,
                "cloneUrl": clone_url,
                "branch": effective_branch,
                "path": effective_path,
            },
            "imported": imported,
        }
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)
    finally:
        if clone_root is not None:
            try:
                cleanup_root = clone_root.parent
                if cleanup_root.name.startswith("forgepilot-skill-import-"):
                    shutil.rmtree(cleanup_root, ignore_errors=True)
            except Exception:
                pass


@router.post("/import-skill/self-check")
async def import_skill_self_check(request: Request, body: dict | None = None) -> dict:
    denied = _authorize_files_scope(request, PERM_FILES_IMPORT_SKILL_SELF_CHECK)
    if denied:
        return denied
    payload = body or {}
    provided_url = str(payload.get("url") or "").strip()
    provided_branch = str(payload.get("branch") or "").strip()
    provided_path = str(payload.get("path") or "").strip()

    raw_url = provided_url or _DEFAULT_IMPORT_SELF_CHECK_URL
    if not provided_url and not provided_branch and not provided_path:
        branch_override = _DEFAULT_IMPORT_SELF_CHECK_BRANCH
        path_override = _DEFAULT_IMPORT_SELF_CHECK_PATH
    else:
        branch_override = provided_branch or None
        path_override = provided_path or None

    clone_root: Path | None = None
    try:
        clone_url, branch_from_url, path_from_url = _parse_github_repo_url(raw_url)
        effective_branch = branch_override or branch_from_url
        effective_path = path_override or path_from_url
        clone_root = await asyncio.to_thread(_clone_repo_to_temp, clone_url, effective_branch)
        skill_dirs = _collect_skill_dirs(clone_root, effective_path)
        if not skill_dirs:
            return JSONResponse(
                {"success": False, "error": "No SKILL.md found in repository"},
                status_code=400,
            )

        return {
            "success": True,
            "count": len(skill_dirs),
            "source": {
                "url": raw_url,
                "cloneUrl": clone_url,
                "branch": effective_branch,
                "path": effective_path,
            },
            "sample": {
                "name": skill_dirs[0].name,
                "path": str(skill_dirs[0]),
            },
        }
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)
    finally:
        if clone_root is not None:
            try:
                cleanup_root = clone_root.parent
                if cleanup_root.name.startswith("forgepilot-skill-import-"):
                    shutil.rmtree(cleanup_root, ignore_errors=True)
            except Exception:
                pass


# Existing compatibility endpoint retained.
@router.get("/task/{task_id}")
async def get_files_by_task(request: Request, task_id: str) -> dict:
    denied = _authorize_files_scope(request, PERM_FILES_TASK)
    if denied:
        return denied
    files = await list_files_by_task(task_id)
    return {"files": files}

