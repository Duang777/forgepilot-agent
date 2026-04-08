from __future__ import annotations

import asyncio
import os
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


class NativeProvider(ISandboxProvider):
    type = "native"
    name = "Native (No Isolation)"

    def __init__(self) -> None:
        self._config: dict[str, Any] = {
            "shell": "cmd.exe" if os.name == "nt" else "/bin/bash",
            "defaultTimeout": 120000,
        }

    async def is_available(self) -> bool:
        return True

    async def init(self, config: dict[str, Any] | None = None) -> None:
        if config:
            self._config = {**self._config, **config}

    async def exec(self, options: SandboxExecOptions) -> SandboxExecResult:
        start = time.time()
        args = options.args or []
        cwd = options.cwd or os.getcwd()
        timeout = (options.timeout or self._config.get("defaultTimeout", 120000)) / 1000
        env = {**os.environ, **(options.env or {})}

        def _run() -> subprocess.CompletedProcess:
            return subprocess.run(
                [options.command, *args],
                cwd=cwd,
                env=env,
                text=True,
                capture_output=True,
                timeout=timeout,
                shell=False,
            )

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
        if ext in {".py"}:
            cmd = "python"
            args = [file_path, *((options.args or []) if options else [])]
        elif ext in {".ts", ".mts"}:
            cmd = "npx"
            args = ["tsx", file_path, *((options.args or []) if options else [])]
        elif ext in {".js", ".mjs"}:
            cmd = "node"
            args = [file_path, *((options.args or []) if options else [])]
        else:
            cmd = "python"
            args = [file_path, *((options.args or []) if options else [])]

        return await self.exec(
            SandboxExecOptions(
                command=cmd,
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
            supports_networking=True,
            isolation="none",
            supported_runtimes=["node", "python", "bash", "bun"],
            supports_pooling=False,
        )

    def set_volumes(self, volumes: list[VolumeMount]) -> None:
        del volumes
        return None

