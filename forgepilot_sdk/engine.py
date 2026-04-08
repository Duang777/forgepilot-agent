from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Awaitable, Callable

from forgepilot_sdk.providers.base import LLMProvider, ProviderToolCall
from forgepilot_sdk.types import ConversationMessage, SDKMessage, ToolContext, ToolDefinition


def _tool_to_provider_schema(tool: ToolDefinition) -> ToolDefinition:
    return tool


class QueryEngine:
    def __init__(
        self,
        *,
        provider: LLMProvider,
        model: str,
        tools: list[ToolDefinition],
        cwd: Path,
        max_turns: int = 20,
        system_prompt: str | None = None,
        append_system_prompt: str | None = None,
        session_id: str | None = None,
        skill_registry: dict[str, dict[str, Any]] | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        permission_mode: str = "bypassPermissions",
        on_permission_request: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        wait_for_permission_decision: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.tools = [_tool_to_provider_schema(t) for t in tools]
        self.cwd = cwd
        self.max_turns = max_turns
        self.system_prompt = system_prompt
        self.append_system_prompt = append_system_prompt
        self.messages: list[ConversationMessage] = []
        self.session_id = session_id or str(uuid.uuid4())
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        self.cost = 0.0
        self.mcp_servers = mcp_servers or []
        self.permission_mode = permission_mode
        self.on_permission_request = on_permission_request
        self.wait_for_permission_decision = wait_for_permission_decision
        self.tool_context = ToolContext(
            cwd=cwd,
            state={"skill_registry": skill_registry or {}, "mcp_servers": self.mcp_servers},
            provider=self.provider,
            model=self.model,
            api_type=getattr(self.provider, "api_type", None),
        )
        self._tool_map = {tool.name: tool for tool in tools}

    def _requires_permission(self, tool: ToolDefinition) -> bool:
        if self.permission_mode == "bypassPermissions":
            return False
        return not bool(tool.read_only)

    def _build_system_prompt(self) -> str:
        if self.system_prompt:
            base = self.system_prompt
        else:
            lines = [
                "You are an AI assistant with access to tools.",
                "Use tools when needed for correctness and efficiency.",
                "",
                "# Available Tools",
            ]
            for t in self.tools:
                lines.append(f"- {t.name}: {t.description}")
            lines.extend(["", f"# Working Directory\n{self.cwd}"])
            base = "\n".join(lines)
        if self.append_system_prompt:
            return f"{base}\n\n{self.append_system_prompt}"
        return base

    async def _execute_tool(self, call: ProviderToolCall) -> SDKMessage:
        tool = self._tool_map.get(call.name)
        if not tool:
            output = f"Tool not found: {call.name}"
            return {
                "type": "tool_result",
                "result": {"tool_use_id": call.id, "tool_name": call.name, "output": output, "is_error": True},
            }
        try:
            result = await tool.call(call.input, self.tool_context)
            output = (
                result.content
                if isinstance(result.content, str)
                else json.dumps(result.content, ensure_ascii=False)
            )
            return {
                "type": "tool_result",
                "result": {
                    "tool_use_id": call.id,
                    "tool_name": call.name,
                    "output": output,
                    "is_error": bool(result.is_error),
                },
            }
        except Exception as exc:
            return {
                "type": "tool_result",
                "result": {
                    "tool_use_id": call.id,
                    "tool_name": call.name,
                    "output": f"{type(exc).__name__}: {exc}",
                    "is_error": True,
                },
            }

    async def submit_message(self, prompt: str) -> AsyncGenerator[SDKMessage, None]:
        self.messages.append(ConversationMessage(role="user", content=prompt))

        yield {
            "type": "system",
            "subtype": "init",
            "session_id": self.session_id,
            "tools": [t.name for t in self.tools],
            "model": self.model,
            "cwd": str(self.cwd),
            "mcp_servers": self.mcp_servers,
            "permission_mode": self.permission_mode,
        }

        system_prompt = self._build_system_prompt()
        turns = 0
        errors: list[str] = []
        while turns < self.max_turns:
            turns += 1
            response = await self.provider.create_message(
                model=self.model,
                system_prompt=system_prompt,
                messages=self.messages,
                tools=self.tools,
            )
            self.usage["input_tokens"] += int(response.usage.get("input_tokens", 0))
            self.usage["output_tokens"] += int(response.usage.get("output_tokens", 0))

            blocks: list[dict[str, Any]] = []
            if response.content:
                blocks.append({"type": "text", "text": response.content})
            for call in response.tool_calls:
                blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.input})
            if blocks:
                yield {
                    "type": "assistant",
                    "session_id": self.session_id,
                    "message": {"role": "assistant", "content": blocks},
                }

            if response.content or response.tool_calls:
                if response.tool_calls:
                    self.messages.append(
                        ConversationMessage(
                            role="assistant",
                            content={
                                "text": response.content,
                                "tool_calls": [
                                    {"id": call.id, "name": call.name, "input": call.input}
                                    for call in response.tool_calls
                                ],
                            },
                        )
                    )
                else:
                    self.messages.append(ConversationMessage(role="assistant", content=response.content))

            if not response.tool_calls:
                yield {
                    "type": "result",
                    "subtype": "success",
                    "session_id": self.session_id,
                    "is_error": False,
                    "num_turns": turns,
                    "total_cost_usd": self.cost,
                    "usage": self.usage,
                }
                return

            read_only_calls = [
                c for c in response.tool_calls if self._tool_map.get(c.name) and self._tool_map[c.name].read_only
            ]

            tool_events: list[SDKMessage] = []
            if len(read_only_calls) == len(response.tool_calls):
                # If all tool calls are read-only, run concurrently.
                tool_events = await asyncio.gather(*[self._execute_tool(call) for call in response.tool_calls])
            else:
                for call in response.tool_calls:
                    tool = self._tool_map.get(call.name)
                    if (
                        tool
                        and self._requires_permission(tool)
                        and self.wait_for_permission_decision is not None
                    ):
                        permission_id = str(uuid.uuid4())
                        permission_payload = {
                            "id": permission_id,
                            "toolUseId": call.id,
                            "toolName": call.name,
                            "input": call.input,
                            "message": f"Allow tool '{call.name}'?",
                        }
                        if self.on_permission_request is not None:
                            try:
                                await self.on_permission_request(permission_payload)
                            except Exception:
                                pass
                        yield {
                            "type": "system",
                            "subtype": "permission_request",
                            "session_id": self.session_id,
                            "permission": permission_payload,
                        }

                        approved = False
                        try:
                            approved = await asyncio.wait_for(
                                self.wait_for_permission_decision(permission_id),
                                timeout=600,
                            )
                        except Exception:
                            approved = False

                        if not approved:
                            tool_events.append(
                                {
                                    "type": "tool_result",
                                    "result": {
                                        "tool_use_id": call.id,
                                        "tool_name": call.name,
                                        "output": "Permission denied by user",
                                        "is_error": True,
                                    },
                                }
                            )
                            continue

                    tool_events.append(await self._execute_tool(call))

            for event in tool_events:
                yield event
                self.messages.append(
                    ConversationMessage(
                        role="tool",
                        content={
                            "tool_call_id": event["result"]["tool_use_id"],
                            "content": event["result"]["output"],
                            "is_error": bool(event["result"].get("is_error")),
                        },
                    )
                )
                if event["result"].get("is_error"):
                    errors.append(event["result"]["output"])

        yield {
            "type": "result",
            "subtype": "error_max_turns",
            "session_id": self.session_id,
            "is_error": True,
            "num_turns": turns,
            "total_cost_usd": self.cost,
            "usage": self.usage,
            "errors": errors or ["max turns exceeded"],
        }

