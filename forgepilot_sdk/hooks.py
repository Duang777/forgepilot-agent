from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

HookEvent = Literal[
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "SessionStart",
    "SessionEnd",
    "Stop",
    "SubagentStart",
    "SubagentStop",
    "UserPromptSubmit",
    "PermissionRequest",
    "PermissionDenied",
    "TaskCreated",
    "TaskCompleted",
    "ConfigChange",
    "CwdChanged",
    "FileChanged",
    "Notification",
    "PreCompact",
    "PostCompact",
    "TeammateIdle",
]

HOOK_EVENTS: tuple[HookEvent, ...] = (
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "SessionStart",
    "SessionEnd",
    "Stop",
    "SubagentStart",
    "SubagentStop",
    "UserPromptSubmit",
    "PermissionRequest",
    "PermissionDenied",
    "TaskCreated",
    "TaskCompleted",
    "ConfigChange",
    "CwdChanged",
    "FileChanged",
    "Notification",
    "PreCompact",
    "PostCompact",
    "TeammateIdle",
)


HookInput = dict[str, Any]
HookOutput = dict[str, Any]
HookHandler = Callable[[HookInput], Awaitable[HookOutput | None]]


@dataclass(slots=True)
class HookDefinition:
    command: str | None = None
    handler: HookHandler | None = None
    matcher: str | None = None
    timeout: int | None = None


HookConfig = dict[str, list[HookDefinition | dict[str, Any]]]


async def _execute_shell_hook(command: str, input_payload: HookInput, timeout_ms: int) -> HookOutput | None:
    env = os.environ.copy()
    env["HOOK_EVENT"] = str(input_payload.get("event", ""))
    env["HOOK_TOOL_NAME"] = str(input_payload.get("toolName", ""))
    env["HOOK_SESSION_ID"] = str(input_payload.get("sessionId", ""))
    env["HOOK_CWD"] = str(input_payload.get("cwd", ""))

    if os.name == "nt":
        exec_cmd = ["powershell", "-NoProfile", "-Command", command]
    else:
        exec_cmd = ["bash", "-lc", command]

    try:
        proc = await asyncio.create_subprocess_exec(
            *exec_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        stdin_bytes = json.dumps(input_payload, ensure_ascii=False).encode("utf-8")
        stdout, _stderr = await asyncio.wait_for(proc.communicate(stdin_bytes), timeout=timeout_ms / 1000.0)
    except Exception:
        return None

    if proc.returncode != 0:
        return None

    text = stdout.decode("utf-8", errors="ignore").strip()
    if not text:
        return None

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"message": text}
    except Exception:
        return {"message": text}


class HookRegistry:
    def __init__(self) -> None:
        self._hooks: dict[HookEvent, list[HookDefinition]] = {event: [] for event in HOOK_EVENTS}

    def register_from_config(self, config: HookConfig) -> None:
        for event, definitions in config.items():
            if event not in HOOK_EVENTS:
                continue
            for definition in definitions:
                if isinstance(definition, HookDefinition):
                    self.register(event, definition)  # type: ignore[arg-type]
                    continue
                if isinstance(definition, dict):
                    self.register(
                        event,  # type: ignore[arg-type]
                        HookDefinition(
                            command=definition.get("command"),
                            handler=definition.get("handler"),
                            matcher=definition.get("matcher"),
                            timeout=definition.get("timeout"),
                        ),
                    )

    def registerFromConfig(self, config: HookConfig) -> None:
        self.register_from_config(config)

    def register(self, event: HookEvent, definition: HookDefinition) -> None:
        self._hooks[event].append(definition)

    def has_hooks(self, event: HookEvent) -> bool:
        return bool(self._hooks.get(event))

    def hasHooks(self, event: HookEvent) -> bool:
        return self.has_hooks(event)

    def clear(self) -> None:
        for event in HOOK_EVENTS:
            self._hooks[event] = []

    async def execute(self, event: HookEvent, input_payload: HookInput) -> list[HookOutput]:
        definitions = self._hooks.get(event, [])
        if not definitions:
            return []

        results: list[HookOutput] = []
        for definition in definitions:
            if definition.matcher and input_payload.get("toolName"):
                try:
                    if not re.search(definition.matcher, str(input_payload.get("toolName"))):
                        continue
                except re.error:
                    continue

            timeout_ms = int(definition.timeout or 30000)
            try:
                output: HookOutput | None
                if definition.handler is not None:
                    output = await asyncio.wait_for(definition.handler(input_payload), timeout=timeout_ms / 1000.0)
                elif definition.command:
                    output = await _execute_shell_hook(definition.command, input_payload, timeout_ms)
                else:
                    output = None
            except Exception:
                output = None

            if output:
                results.append(output)

        return results

    async def executeHooks(self, event: HookEvent, input_payload: HookInput) -> list[HookOutput]:
        return await self.execute(event, input_payload)


def create_hook_registry(config: HookConfig | None = None) -> HookRegistry:
    registry = HookRegistry()
    if config:
        registry.register_from_config(config)
    return registry


def createHookRegistry(config: HookConfig | None = None) -> HookRegistry:
    return create_hook_registry(config)
