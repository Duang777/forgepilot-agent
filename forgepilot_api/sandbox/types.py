from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

SandboxProviderType = Literal["native", "codex", "claude"] | str
IsolationType = Literal["vm", "container", "process", "none"]


@dataclass(slots=True)
class SandboxCapabilities:
    supports_volume_mounts: bool
    supports_networking: bool
    isolation: IsolationType
    supported_runtimes: list[str]
    supports_pooling: bool


@dataclass(slots=True)
class SandboxExecOptions:
    command: str
    args: list[str] | None = None
    cwd: str | None = None
    env: dict[str, str] | None = None
    timeout: int | None = None
    image: str | None = None


@dataclass(slots=True)
class ScriptOptions:
    args: list[str] | None = None
    env: dict[str, str] | None = None
    timeout: int | None = None
    packages: list[str] | None = None


@dataclass(slots=True)
class VolumeMount:
    host_path: str
    guest_path: str
    read_only: bool = False


@dataclass(slots=True)
class SandboxExecResult:
    stdout: str
    stderr: str
    exit_code: int
    duration: int


class ISandboxProvider(Protocol):
    type: SandboxProviderType
    name: str

    async def is_available(self) -> bool:
        ...

    async def init(self, config: dict[str, Any] | None = None) -> None:
        ...

    async def exec(self, options: SandboxExecOptions) -> SandboxExecResult:
        ...

    async def run_script(self, file_path: str, work_dir: str, options: ScriptOptions | None = None) -> SandboxExecResult:
        ...

    async def stop(self) -> None:
        ...

    async def shutdown(self) -> None:
        ...

    def get_capabilities(self) -> SandboxCapabilities:
        ...

    def set_volumes(self, volumes: list[VolumeMount]) -> None:
        ...
