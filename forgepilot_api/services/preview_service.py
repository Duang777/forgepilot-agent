from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass(slots=True)
class PreviewState:
    task_id: str
    status: str
    work_dir: str
    port: int
    url: str
    error: str | None = None


@dataclass(slots=True)
class PreviewProcess:
    state: PreviewState
    process: asyncio.subprocess.Process
    log_task: asyncio.Task | None = None


_preview_map: dict[str, PreviewProcess] = {}


def is_node_available() -> bool:
    return shutil.which("node") is not None and (
        shutil.which("pnpm") is not None
        or shutil.which("npm") is not None
        or shutil.which("yarn") is not None
    )


def _pick_package_manager(work_dir: Path) -> tuple[str, list[str]]:
    if (work_dir / "pnpm-lock.yaml").exists() and shutil.which("pnpm"):
        return "pnpm", ["pnpm", "dev", "--host", "127.0.0.1", "--port"]
    if (work_dir / "yarn.lock").exists() and shutil.which("yarn"):
        return "yarn", ["yarn", "dev", "--host", "127.0.0.1", "--port"]
    if shutil.which("npm"):
        return "npm", ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port"]
    raise RuntimeError("No package manager found (pnpm/yarn/npm)")


async def _wait_server(url: str, timeout_sec: float = 30.0) -> bool:
    deadline = __import__("time").time() + timeout_sec
    async with httpx.AsyncClient(timeout=2.0) as client:
        while __import__("time").time() < deadline:
            try:
                resp = await client.get(url)
                if resp.status_code < 500:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.5)
    return False


async def _stream_logs(task_id: str, process: asyncio.subprocess.Process) -> None:
    if process.stdout is None and process.stderr is None:
        return

    async def _read_stream(stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode(errors="replace").rstrip()
            if text and task_id in _preview_map:
                # store latest line as soft diagnostics
                _preview_map[task_id].state.error = text if "error" in text.lower() else _preview_map[task_id].state.error

    await asyncio.gather(_read_stream(process.stdout), _read_stream(process.stderr))


def _kill_process_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
    else:
        try:
            os.killpg(os.getpgid(pid), 9)
        except Exception:
            try:
                os.kill(pid, 9)
            except Exception:
                pass


async def start_preview(task_id: str, work_dir: str, port: int | None = None) -> dict[str, Any]:
    root = Path(work_dir).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return {
            "status": "error",
            "taskId": task_id,
            "workDir": str(root),
            "previewUrl": None,
            "error": f"workDir does not exist: {root}",
        }

    if not is_node_available():
        return {
            "status": "error",
            "taskId": task_id,
            "workDir": str(root),
            "previewUrl": None,
            "error": "Node.js or package manager (pnpm/yarn/npm) is not available",
        }

    if task_id in _preview_map:
        current = _preview_map[task_id]
        if current.process.returncode is None:
            return {
                "status": current.state.status,
                "taskId": current.state.task_id,
                "workDir": current.state.work_dir,
                "port": current.state.port,
                "previewUrl": current.state.url,
                "error": current.state.error,
            }
        await stop_preview(task_id)

    final_port = int(port or 5173)
    preview_url = f"http://127.0.0.1:{final_port}"

    try:
        _, base_cmd = _pick_package_manager(root)
        cmd = [*base_cmd, str(final_port)]

        kwargs: dict[str, Any] = {
            "cwd": str(root),
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "env": {**os.environ},
        }
        if os.name != "nt":
            kwargs["preexec_fn"] = os.setsid

        proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)

        state = PreviewState(
            task_id=task_id,
            status="starting",
            work_dir=str(root),
            port=final_port,
            url=preview_url,
            error=None,
        )
        log_task = asyncio.create_task(_stream_logs(task_id, proc))
        _preview_map[task_id] = PreviewProcess(state=state, process=proc, log_task=log_task)

        ready = await _wait_server(preview_url)
        if ready and proc.returncode is None:
            _preview_map[task_id].state.status = "running"
        else:
            _preview_map[task_id].state.status = "error"
            _preview_map[task_id].state.error = _preview_map[task_id].state.error or "Preview server failed to start"

        s = _preview_map[task_id].state
        return {
            "status": s.status,
            "taskId": s.task_id,
            "workDir": s.work_dir,
            "port": s.port,
            "previewUrl": s.url,
            "error": s.error,
        }
    except Exception as exc:
        return {
            "status": "error",
            "taskId": task_id,
            "workDir": str(root),
            "previewUrl": preview_url,
            "error": str(exc),
        }


async def stop_preview(task_id: str) -> dict[str, Any]:
    if task_id not in _preview_map:
        return {"status": "idle", "taskId": task_id}

    entry = _preview_map[task_id]
    proc = entry.process
    if proc.returncode is None:
        _kill_process_tree(proc.pid)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:
            pass

    if entry.log_task and not entry.log_task.done():
        entry.log_task.cancel()

    entry.state.status = "stopped"
    entry.state.error = None
    return {
        "status": "stopped",
        "taskId": entry.state.task_id,
        "workDir": entry.state.work_dir,
        "port": entry.state.port,
        "previewUrl": entry.state.url,
        "error": None,
    }


def get_status(task_id: str) -> dict[str, Any]:
    if task_id not in _preview_map:
        return {"status": "idle", "taskId": task_id}

    entry = _preview_map[task_id]
    if entry.process.returncode is not None and entry.state.status == "running":
        entry.state.status = "error"
        if not entry.state.error:
            entry.state.error = f"Preview process exited with code {entry.process.returncode}"

    s = entry.state
    return {
        "status": s.status,
        "taskId": s.task_id,
        "workDir": s.work_dir,
        "port": s.port,
        "previewUrl": s.url,
        "error": s.error,
    }


async def stop_all() -> None:
    ids = list(_preview_map.keys())
    for task_id in ids:
        await stop_preview(task_id)
