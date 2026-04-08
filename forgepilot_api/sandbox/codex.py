from __future__ import annotations

import asyncio
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from forgepilot_api.sandbox.types import (
    ISandboxProvider,
    SandboxCapabilities,
    SandboxExecOptions,
    SandboxExecResult,
    ScriptOptions,
    VolumeMount,
)


def _find_codex_path() -> str | None:
    env_path = os.getenv("CODEX_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    return shutil.which("codex")


class CodexProvider(ISandboxProvider):
    type = "codex"
    name = "Codex CLI Sandbox"

    def __init__(self) -> None:
        self._codex_path: str | None = None
        self._config: dict[str, Any] = {}

    async def is_available(self) -> bool:
        self._codex_path = _find_codex_path()
        return self._codex_path is not None

    async def init(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self._codex_path = _find_codex_path()

    async def exec(self, options: SandboxExecOptions) -> SandboxExecResult:
        start = time.time()
        if not self._codex_path:
            return SandboxExecResult(stdout="", stderr="Codex CLI is not installed", exit_code=1, duration=0)

        args = options.args or []
        cwd = options.cwd or os.getcwd()
        timeout = (options.timeout or 120000) / 1000
        env = {**os.environ, **(options.env or {})}

        sys_name = platform.system().lower()
        platform_subcmd = "macos" if "darwin" in sys_name else "linux"
        cmd = [self._codex_path, "sandbox", platform_subcmd, "--full-auto", "--", options.command, *args]

        def _run() -> subprocess.CompletedProcess:
            return subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout)

        try:
            cp = await asyncio.to_thread(_run)
            return SandboxExecResult(
                stdout=cp.stdout,
                stderr=cp.stderr,
                exit_code=cp.returncode,
                duration=int((time.time() - start) * 1000),
            )
        except Exception as exc:
            return SandboxExecResult(
                stdout="",
                stderr=str(exc),
                exit_code=1,
                duration=int((time.time() - start) * 1000),
            )

    async def run_script(self, file_path: str, work_dir: str, options: ScriptOptions | None = None) -> SandboxExecResult:
        ext = Path(file_path).suffix.lower()
        if ext == ".py":
            command = "python"
            args = [file_path, *((options.args or []) if options else [])]
        elif ext in {".js", ".mjs"}:
            command = "node"
            args = [file_path, *((options.args or []) if options else [])]
        else:
            command = "python"
            args = [file_path, *((options.args or []) if options else [])]
        return await self.exec(
            SandboxExecOptions(
                command=command,
                args=args,
                cwd=work_dir,
                env=(options.env if options else None),
                timeout=(options.timeout if options else None),
            )
        )

    async def stop(self) -> None:
        return None

    async def shutdown(self) -> None:
        await self.stop()

    def get_capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            supports_volume_mounts=False,
            supports_networking=False,
            isolation="process",
            supported_runtimes=["node", "python", "bash"],
            supports_pooling=False,
        )

    def set_volumes(self, volumes: list[VolumeMount]) -> None:
        del volumes
        return None

