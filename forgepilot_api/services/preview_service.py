from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import httpx

PreviewState = Literal["starting", "running", "stopped", "error"]

PORT_RANGE_START = 5173
PORT_RANGE_END = 5273
MAX_CONCURRENT_PREVIEWS = 5
IDLE_TIMEOUT_SECONDS = 30 * 60
HEALTH_CHECK_INTERVAL_SECONDS = 10
STARTUP_TIMEOUT_SECONDS = 120
SERVER_READY_POLL_SECONDS = 1

DEFAULT_PACKAGE_JSON = {
    "name": "preview",
    "type": "module",
    "scripts": {"dev": "vite"},
    "devDependencies": {"vite": "~5.4.0"},
}


def _generate_vite_config(port: int) -> str:
    return (
        "export default {\n"
        "  server: {\n"
        "    host: '0.0.0.0',\n"
        f"    port: {port},\n"
        "    strictPort: true,\n"
        "    watch: {\n"
        "      usePolling: true,\n"
        "    },\n"
        "  },\n"
        "  appType: 'mpa',\n"
        "};\n"
    )


@dataclass(slots=True)
class PreviewInstance:
    id: str
    task_id: str
    work_dir: Path
    port: int
    status: PreviewState
    started_at: datetime
    last_accessed_at: datetime
    error: str | None = None
    process: asyncio.subprocess.Process | None = None
    startup_task: asyncio.Task | None = None
    health_task: asyncio.Task | None = None
    idle_task: asyncio.Task | None = None
    stdout_task: asyncio.Task | None = None
    stderr_task: asyncio.Task | None = None


def is_node_available() -> bool:
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True)
        subprocess.run(["npm", "--version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


class PreviewManager:
    def __init__(self) -> None:
        self._instances: dict[str, PreviewInstance] = {}
        self._used_ports: set[int] = set()
        self._lock = asyncio.Lock()

    async def start_preview(self, task_id: str, work_dir: str, port: int | None = None) -> dict[str, Any]:
        async with self._lock:
            existing = self._instances.get(task_id)
            if existing and existing.status in {"running", "starting"}:
                existing.last_accessed_at = datetime.utcnow()
                self._reset_idle_timeout(existing)
                return self._to_status(existing)

            root = Path(work_dir).expanduser().resolve()
            if not root.exists() or not root.is_dir():
                return self._error_status(task_id, f"workDir does not exist: {root}")
            if not is_node_available():
                return self._error_status(task_id, "Node.js/npm is not available")

            running_count = sum(1 for item in self._instances.values() if item.status in {"running", "starting"})
            if running_count >= MAX_CONCURRENT_PREVIEWS:
                oldest = self._find_oldest_idle_preview()
                if oldest is not None:
                    await self._stop_preview_internal(oldest.task_id)
                else:
                    return self._error_status(
                        task_id,
                        f"Maximum concurrent previews ({MAX_CONCURRENT_PREVIEWS}) reached. Please stop an existing preview first.",
                    )

            allocated = self._allocate_port(port)
            if allocated is None:
                return self._error_status(task_id, f"No available ports in range {PORT_RANGE_START}-{PORT_RANGE_END}")

            instance = PreviewInstance(
                id=f"preview-{task_id}",
                task_id=task_id,
                work_dir=root,
                port=allocated,
                status="starting",
                started_at=datetime.utcnow(),
                last_accessed_at=datetime.utcnow(),
            )
            self._instances[task_id] = instance
            instance.startup_task = asyncio.create_task(self._start_vite_server(instance))
            return self._to_status(instance)

    async def startPreview(self, config: dict[str, Any]) -> dict[str, Any]:
        return await self.start_preview(
            str(config.get("taskId") or ""),
            str(config.get("workDir") or ""),
            int(config["port"]) if config.get("port") is not None else None,
        )

    async def stop_preview(self, task_id: str) -> dict[str, Any]:
        async with self._lock:
            if task_id not in self._instances:
                return self._stopped_status(task_id)
        return await self._stop_preview_internal(task_id)

    async def stopPreview(self, task_id: str) -> dict[str, Any]:
        return await self.stop_preview(task_id)

    async def _stop_preview_internal(self, task_id: str) -> dict[str, Any]:
        instance = self._instances.get(task_id)
        if instance is None:
            return self._stopped_status(task_id)
        await self._cleanup_instance(instance)
        instance.status = "stopped"
        instance.error = None
        return self._to_status(instance)

    def get_status(self, task_id: str) -> dict[str, Any]:
        instance = self._instances.get(task_id)
        if instance is None:
            return self._stopped_status(task_id)
        instance.last_accessed_at = datetime.utcnow()
        self._reset_idle_timeout(instance)
        return self._to_status(instance)

    def getStatus(self, task_id: str) -> dict[str, Any]:
        return self.get_status(task_id)

    async def stop_all(self) -> None:
        tasks = [self._stop_preview_internal(task_id) for task_id in list(self._instances.keys())]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def stopAll(self) -> None:
        await self.stop_all()

    async def _start_vite_server(self, instance: PreviewInstance) -> None:
        try:
            await self._ensure_project_files(instance.work_dir, instance.port)
            await self._start_vite_process(instance)
        except Exception as exc:
            instance.status = "error"
            instance.error = str(exc)
            await self._cleanup_instance(instance)

    async def _ensure_project_files(self, work_dir: Path, port: int) -> None:
        package_json_path = work_dir / "package.json"
        if not package_json_path.exists():
            package_json_path.write_text(json.dumps(DEFAULT_PACKAGE_JSON, indent=2), encoding="utf-8")

        for name in ("vite.config.ts", "vite.config.mts", "vite.config.mjs"):
            path = work_dir / name
            if path.exists():
                try:
                    path.unlink()
                except Exception:
                    pass

        (work_dir / "vite.config.js").write_text(_generate_vite_config(port), encoding="utf-8")

        index_html = work_dir / "index.html"
        if index_html.exists():
            return

        html_files = [p for p in work_dir.iterdir() if p.is_file() and p.suffix.lower() == ".html"]
        fallback = next((p for p in html_files if p.name != "index.html"), None)
        if fallback is not None:
            index_html.write_text(
                "<!DOCTYPE html>\n"
                "<html>\n"
                "<head>\n"
                f"  <meta http-equiv=\"refresh\" content=\"0; url='./{fallback.name}'\">\n"
                "</head>\n"
                "<body>\n"
                f"  <p>Redirecting to <a href=\"./{fallback.name}\">{fallback.name}</a>...</p>\n"
                "</body>\n"
                "</html>\n",
                encoding="utf-8",
            )

    async def _start_vite_process(self, instance: PreviewInstance) -> None:
        work_dir = instance.work_dir
        vite_cmd = work_dir / "node_modules" / ".bin" / "vite"
        vite_cmd_win = work_dir / "node_modules" / ".bin" / "vite.cmd"
        vite_cli_js = work_dir / "node_modules" / "vite" / "bin" / "vite.js"
        needs_install = not (vite_cmd.exists() or vite_cmd_win.exists() or vite_cli_js.exists())

        if needs_install:
            await self._run_npm_install(work_dir)

        if vite_cli_js.exists():
            cmd = ["node", str(vite_cli_js)]
        else:
            cmd = ["npx", "vite"]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(work_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        instance.process = process
        instance.stdout_task = asyncio.create_task(self._consume_output(instance, process.stdout))
        instance.stderr_task = asyncio.create_task(self._consume_output(instance, process.stderr))
        asyncio.create_task(self._watch_process_exit(instance))

        ready = await self._wait_for_server_ready(instance.port, STARTUP_TIMEOUT_SECONDS)
        if not ready:
            instance.status = "error"
            instance.error = "Server failed to start within timeout"
            await self._cleanup_instance(instance)
            return

        if instance.task_id not in self._instances:
            return

        instance.status = "running"
        self._start_health_check(instance)
        self._reset_idle_timeout(instance)

    async def _run_npm_install(self, work_dir: Path) -> None:
        process = await asyncio.create_subprocess_exec(
            "npm",
            "install",
            cwd=str(work_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=STARTUP_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            await self._terminate_process(process)
            raise RuntimeError("npm install timed out after 2 minutes")
        if process.returncode != 0:
            stderr = ""
            if process.stderr is not None:
                stderr = (await process.stderr.read()).decode("utf-8", errors="replace")
            raise RuntimeError(f"npm install failed (exit code {process.returncode}): {stderr.strip()}")

    async def _consume_output(
        self,
        instance: PreviewInstance,
        stream: asyncio.StreamReader | None,
    ) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            lowered = text.lower()
            if "error" in lowered and instance.status not in {"stopped"}:
                instance.error = text

    async def _watch_process_exit(self, instance: PreviewInstance) -> None:
        process = instance.process
        if process is None:
            return
        code = await process.wait()
        if instance.status in {"running", "starting"} and instance.task_id in self._instances:
            instance.status = "stopped"
            if code != 0 and not instance.error:
                instance.error = f"Vite process exited with code {code}"
            await self._cleanup_instance(instance)

    async def _wait_for_server_ready(self, port: int, timeout_seconds: int) -> bool:
        deadline = asyncio.get_event_loop().time() + float(timeout_seconds)
        async with httpx.AsyncClient(timeout=3.0) as client:
            while asyncio.get_event_loop().time() < deadline:
                try:
                    response = await client.get(f"http://localhost:{port}")
                    if response.is_success or response.status_code == 404:
                        return True
                except Exception:
                    pass
                await asyncio.sleep(SERVER_READY_POLL_SECONDS)
        return False

    def _start_health_check(self, instance: PreviewInstance) -> None:
        if instance.health_task and not instance.health_task.done():
            instance.health_task.cancel()
        instance.health_task = asyncio.create_task(self._health_loop(instance.task_id))

    async def _health_loop(self, task_id: str) -> None:
        async with httpx.AsyncClient(timeout=3.0) as client:
            while True:
                await asyncio.sleep(HEALTH_CHECK_INTERVAL_SECONDS)
                instance = self._instances.get(task_id)
                if instance is None or instance.status != "running":
                    return
                try:
                    response = await client.head(f"http://localhost:{instance.port}")
                    if not response.is_success and response.status_code != 404:
                        raise RuntimeError(f"Health check failed: {response.status_code}")
                except Exception:
                    instance.status = "error"
                    instance.error = "Server health check failed"
                    await self._cleanup_instance(instance)
                    return

    def _reset_idle_timeout(self, instance: PreviewInstance) -> None:
        if instance.idle_task and not instance.idle_task.done():
            instance.idle_task.cancel()
        instance.idle_task = asyncio.create_task(self._idle_timeout(instance.task_id))

    async def _idle_timeout(self, task_id: str) -> None:
        await asyncio.sleep(IDLE_TIMEOUT_SECONDS)
        if task_id in self._instances:
            await self._stop_preview_internal(task_id)

    async def _cleanup_instance(self, instance: PreviewInstance) -> None:
        for task in (instance.startup_task, instance.health_task, instance.idle_task, instance.stdout_task, instance.stderr_task):
            if task and not task.done() and task is not asyncio.current_task():
                task.cancel()
        instance.startup_task = None
        instance.health_task = None
        instance.idle_task = None
        instance.stdout_task = None
        instance.stderr_task = None

        if instance.process is not None and instance.process.returncode is None:
            await self._terminate_process(instance.process)
        instance.process = None

        self._release_port(instance.port)
        self._instances.pop(instance.task_id, None)

    async def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            return
        except Exception:
            try:
                process.kill()
            except Exception:
                return
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _allocate_port(self, preferred: int | None = None) -> int | None:
        if preferred is not None and PORT_RANGE_START <= int(preferred) <= PORT_RANGE_END:
            p = int(preferred)
            if p not in self._used_ports:
                self._used_ports.add(p)
                return p
        for port in range(PORT_RANGE_START, PORT_RANGE_END + 1):
            if port not in self._used_ports:
                self._used_ports.add(port)
                return port
        return None

    def _release_port(self, port: int) -> None:
        self._used_ports.discard(int(port))

    def _find_oldest_idle_preview(self) -> PreviewInstance | None:
        oldest: PreviewInstance | None = None
        for instance in self._instances.values():
            if instance.status != "running":
                continue
            if oldest is None or instance.last_accessed_at < oldest.last_accessed_at:
                oldest = instance
        return oldest

    def _stopped_status(self, task_id: str) -> dict[str, Any]:
        return {
            "id": f"preview-{task_id}",
            "taskId": task_id,
            "status": "stopped",
            "url": None,
            "hostPort": None,
            "error": None,
            "startedAt": None,
            "lastAccessedAt": None,
            "previewUrl": None,
            "port": None,
            "workDir": None,
        }

    def _error_status(self, task_id: str, message: str) -> dict[str, Any]:
        return {
            "id": f"preview-{task_id}",
            "taskId": task_id,
            "status": "error",
            "url": None,
            "hostPort": None,
            "error": message,
            "startedAt": None,
            "lastAccessedAt": None,
            "previewUrl": None,
            "port": None,
            "workDir": None,
        }

    def _to_status(self, instance: PreviewInstance) -> dict[str, Any]:
        url = f"http://localhost:{instance.port}" if instance.status == "running" else None
        return {
            "id": instance.id,
            "taskId": instance.task_id,
            "status": instance.status,
            "url": url,
            "hostPort": instance.port,
            "error": instance.error,
            "startedAt": instance.started_at.isoformat(),
            "lastAccessedAt": instance.last_accessed_at.isoformat(),
            "previewUrl": f"http://127.0.0.1:{instance.port}",
            "port": instance.port,
            "workDir": str(instance.work_dir),
        }


_preview_manager: PreviewManager | None = None


def get_preview_manager() -> PreviewManager:
    global _preview_manager
    if _preview_manager is None:
        _preview_manager = PreviewManager()
    return _preview_manager


def getPreviewManager() -> PreviewManager:
    return get_preview_manager()


async def start_preview(task_id: str, work_dir: str, port: int | None = None) -> dict[str, Any]:
    return await get_preview_manager().start_preview(task_id, work_dir, port)


async def stop_preview(task_id: str) -> dict[str, Any]:
    return await get_preview_manager().stop_preview(task_id)


def get_status(task_id: str) -> dict[str, Any]:
    return get_preview_manager().get_status(task_id)


async def stop_all() -> None:
    await get_preview_manager().stop_all()


async def startPreview(config: dict[str, Any]) -> dict[str, Any]:
    return await get_preview_manager().startPreview(config)


async def stopPreview(task_id: str) -> dict[str, Any]:
    return await stop_preview(task_id)


def getStatus(task_id: str) -> dict[str, Any]:
    return get_status(task_id)


async def stopAll() -> None:
    await stop_all()
