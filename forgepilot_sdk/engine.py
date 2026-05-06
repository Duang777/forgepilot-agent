from __future__ import annotations

import asyncio
import json
import os
import uuid
from time import perf_counter
from pathlib import Path
from typing import Any, AsyncGenerator, Awaitable, Callable

from forgepilot_sdk.hooks import HookRegistry
from forgepilot_sdk.policy import PolicyDecision, evaluate_tool_policy
from forgepilot_sdk.providers.base import LLMProvider, ProviderResponse, ProviderToolCall
from forgepilot_sdk.types import ConversationMessage, SDKMessage, ThinkingConfig, ToolContext, ToolDefinition
from forgepilot_sdk.utils.compact import (
    AutoCompactState,
    compact_conversation,
    create_auto_compact_state,
    micro_compact_messages,
)
from forgepilot_sdk.utils.context_orchestrator import ContextOrchestrator
from forgepilot_sdk.utils.context import get_system_context, get_user_context
from forgepilot_sdk.utils.messages import normalize_messages_for_api
from forgepilot_sdk.utils.retry import is_prompt_too_long_error, with_retry
from forgepilot_sdk.utils.tokens import estimate_cost


def _is_aborted(signal: Any) -> bool:
    if signal is None:
        return False
    if hasattr(signal, "aborted"):
        try:
            return bool(signal.aborted)
        except Exception:
            return False
    if hasattr(signal, "is_set"):
        try:
            return bool(signal.is_set())
        except Exception:
            return False
    return False


def _resolve_thinking_payload(thinking: ThinkingConfig | None) -> dict[str, Any] | None:
    if not thinking or thinking.type != "enabled":
        return None
    budget = thinking.resolved_budget_tokens()
    if not budget:
        return None
    return {"type": "enabled", "budget_tokens": budget}


def _extract_last_assistant_text(messages: list[ConversationMessage]) -> str:
    for message in reversed(messages):
        if message.role != "assistant":
            continue
        content = message.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
            return "".join(parts).strip()
    return ""


def _try_parse_structured_output(text: str) -> Any | None:
    candidate = text.strip()
    if not candidate:
        return None
    try:
        return json.loads(candidate)
    except Exception:
        return None


class QueryEngine:
    def __init__(
        self,
        *,
        provider: LLMProvider,
        model: str,
        tools: list[ToolDefinition],
        cwd: Path,
        max_turns: int = 20,
        max_budget_usd: float | None = None,
        max_tokens: int = 16384,
        thinking: ThinkingConfig | None = None,
        system_prompt: str | None = None,
        append_system_prompt: str | None = None,
        session_id: str | None = None,
        skill_registry: dict[str, dict[str, Any]] | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        permission_mode: str = "bypassPermissions",
        can_use_tool: Callable[[ToolDefinition, Any], Awaitable[dict[str, Any]]] | None = None,
        include_partial_messages: bool = False,
        abort_signal: Any | None = None,
        on_permission_request: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        wait_for_permission_decision: Callable[[str], Awaitable[bool]] | None = None,
        hook_registry: HookRegistry | None = None,
        json_schema: dict[str, Any] | None = None,
        output_format: dict[str, Any] | None = None,
        fallback_model: str | None = None,
        effort: str | None = None,
        agents: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.tools = tools
        self.cwd = cwd
        self.max_turns = max_turns
        self.max_budget_usd = max_budget_usd
        self.max_tokens = max_tokens
        self.thinking = thinking
        self.system_prompt = system_prompt
        self.append_system_prompt = append_system_prompt
        self.messages: list[ConversationMessage] = []
        self.session_id = session_id or str(uuid.uuid4())
        self.usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        self.cost = 0.0
        self.turn_count = 0
        self.api_time_ms = 0
        self.permission_mode = permission_mode
        self.can_use_tool = can_use_tool
        self.include_partial_messages = include_partial_messages
        self.abort_signal = abort_signal
        self.on_permission_request = on_permission_request
        self.wait_for_permission_decision = wait_for_permission_decision
        self.json_schema = json_schema
        self.output_format = output_format
        self.fallback_model = fallback_model
        self.effort = effort
        self.active_model = model
        self.model_usage: dict[str, dict[str, int]] = {}
        self.mcp_servers = mcp_servers or []
        self.agents = agents or {}
        self.hook_registry = hook_registry
        self.compact_state: AutoCompactState = create_auto_compact_state()
        self.context_orchestrator = ContextOrchestrator(self.provider, self.model)

        self.tool_context = ToolContext(
            cwd=cwd,
            state={"skill_registry": skill_registry or {}, "mcp_servers": self.mcp_servers},
            abort_signal=abort_signal,
            provider=self.provider,
            model=self.model,
            api_type=getattr(self.provider, "api_type", None),
        )
        self._tool_map = {tool.name: tool for tool in tools}

    async def _execute_hooks(self, event: str, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if self.hook_registry is None or not self.hook_registry.has_hooks(event):  # type: ignore[arg-type]
            return []
        payload = {
            "event": event,
            "sessionId": self.session_id,
            "cwd": str(self.cwd),
            "abortSignal": self.abort_signal,
        }
        if extra:
            payload.update(extra)
        try:
            return await self.hook_registry.execute(event, payload)  # type: ignore[arg-type]
        except Exception:
            return []

    async def _build_system_prompt(self) -> str:
        if self.system_prompt:
            base = self.system_prompt
            if self.append_system_prompt:
                return f"{base}\n\n{self.append_system_prompt}"
            return base

        parts = [
            "You are an AI assistant with access to tools. Use the tools provided to help the user accomplish their tasks.",
            "You should use tools when they would help you complete the task more accurately or efficiently.",
            "",
            "# Available Tools",
        ]
        for tool in self.tools:
            parts.append(f"- **{tool.name}**: {tool.description}")
        if self.agents:
            parts.extend(["", "# Available Subagents"])
            for name, definition in self.agents.items():
                if not isinstance(definition, dict):
                    continue
                description = str(definition.get("description") or "").strip() or "No description provided."
                parts.append(f"- **{name}**: {description}")

        try:
            sys_ctx = await get_system_context(str(self.cwd))
            if sys_ctx:
                parts.extend(["", "# Environment", sys_ctx])
        except Exception:
            pass

        try:
            user_ctx = await get_user_context(str(self.cwd))
            if user_ctx:
                parts.extend(["", "# Project Context", user_ctx])
        except Exception:
            pass

        parts.extend(["", f"# Working Directory\n{self.cwd}"])
        if self.append_system_prompt:
            parts.extend(["", self.append_system_prompt])
        return "\n".join(parts)

    def _normalize_provider_response(self, response: ProviderResponse) -> tuple[list[dict[str, Any]], list[ProviderToolCall]]:
        blocks: list[dict[str, Any]] = []
        tool_calls: dict[str, ProviderToolCall] = {}

        content = response.content
        if isinstance(content, str):
            if content:
                blocks.append({"type": "text", "text": content})
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    blocks.append({"type": "text", "text": str(block.get("text") or "")})
                elif block.get("type") == "tool_use":
                    call = ProviderToolCall(
                        id=str(block.get("id") or str(uuid.uuid4())),
                        name=str(block.get("name") or ""),
                        input=block.get("input") if isinstance(block.get("input"), dict) else (block.get("input") or {}),
                    )
                    tool_calls[call.id] = call
                    blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.input})

        for call in response.tool_calls:
            if call.id in tool_calls:
                continue
            tool_calls[call.id] = call
            blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.input})

        return blocks, list(tool_calls.values())

    async def _execute_single_tool(
        self,
        call: ProviderToolCall,
        tool: ToolDefinition | None,
        context: ToolContext,
    ) -> dict[str, Any]:
        if not tool:
            return {
                "type": "tool_result",
                "result": {
                    "tool_use_id": call.id,
                    "tool_name": call.name,
                    "output": f'Error: Unknown tool "{call.name}"',
                    "is_error": True,
                },
            }

        if not tool.is_enabled():
            return {
                "type": "tool_result",
                "result": {
                    "tool_use_id": call.id,
                    "tool_name": call.name,
                    "output": f'Error: Tool "{call.name}" is not enabled',
                    "is_error": True,
                },
            }

        pre_hooks = await self._execute_hooks(
            "PreToolUse",
            {"toolName": call.name, "toolInput": call.input, "toolUseId": call.id},
        )
        if any(bool(h.get("block")) for h in pre_hooks):
            msg = next((str(h.get("message")) for h in pre_hooks if h.get("message")), "Blocked by PreToolUse hook")
            return {
                "type": "tool_result",
                "result": {
                    "tool_use_id": call.id,
                    "tool_name": call.name,
                    "output": msg,
                    "is_error": True,
                },
            }

        try:
            result = await tool.call(call.input if isinstance(call.input, dict) else {"value": call.input}, context)
            output = result.content if isinstance(result.content, str) else str(result.content)
            await self._execute_hooks(
                "PostToolUse",
                {
                    "toolName": call.name,
                    "toolInput": call.input,
                    "toolOutput": output,
                    "toolUseId": call.id,
                },
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
            await self._execute_hooks(
                "PostToolUseFailure",
                {
                    "toolName": call.name,
                    "toolInput": call.input,
                    "toolUseId": call.id,
                    "error": str(exc),
                },
            )
            return {
                "type": "tool_result",
                "result": {
                    "tool_use_id": call.id,
                    "tool_name": call.name,
                    "output": f"Tool execution error: {exc}",
                    "is_error": True,
                },
            }

    async def _execute_tools(self, calls: list[ProviderToolCall]) -> list[dict[str, Any]]:
        context = ToolContext(
            cwd=self.cwd,
            abort_signal=self.abort_signal,
            provider=self.provider,
            model=self.model,
            api_type=getattr(self.provider, "api_type", None),
            state=self.tool_context.state,
        )

        max_concurrency = max(1, int(os.getenv("AGENT_SDK_MAX_TOOL_CONCURRENCY", "10")))
        read_only: list[tuple[ProviderToolCall, ToolDefinition | None]] = []
        mutations: list[tuple[ProviderToolCall, ToolDefinition | None]] = []

        for call in calls:
            tool = self._tool_map.get(call.name)
            if tool and tool.is_read_only():
                read_only.append((call, tool))
            else:
                mutations.append((call, tool))

        results: list[dict[str, Any]] = []
        for i in range(0, len(read_only), max_concurrency):
            batch = read_only[i : i + max_concurrency]
            batch_results = await asyncio.gather(
                *[self._execute_single_tool(call, tool, context) for call, tool in batch]
            )
            results.extend(batch_results)

        for call, tool in mutations:
            results.append(await self._execute_single_tool(call, tool, context))

        return results

    def _requires_permission(self, tool: ToolDefinition) -> bool:
        if self.permission_mode in {"bypassPermissions", "dontAsk", "acceptEdits", "auto"}:
            return False
        return not tool.is_read_only()

    def _policy_denied_result(self, call: ProviderToolCall, reason: str, risk_level: str) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "result": {
                "tool_use_id": call.id,
                "tool_name": call.name,
                "output": f"__POLICY_DENIED__|risk={risk_level}|{reason}",
                "is_error": True,
            },
        }

    async def submit_message(self, prompt: str | list[dict[str, Any]]) -> AsyncGenerator[SDKMessage, None]:
        await self._execute_hooks("SessionStart")
        user_submit_hooks = await self._execute_hooks("UserPromptSubmit", {"toolInput": prompt})
        if any(bool(h.get("block")) for h in user_submit_hooks):
            yield {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "usage": self.usage,
                "num_turns": 0,
                "cost": 0,
                "errors": ["Blocked by UserPromptSubmit hook"],
            }
            return

        self.messages.append(ConversationMessage(role="user", content=prompt))
        system_prompt = await self._build_system_prompt()

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

        turns_remaining = self.max_turns
        budget_exceeded = False
        max_output_recovery_attempts = 0
        max_output_recovery = 3
        errors: list[str] = []

        while turns_remaining > 0:
            if _is_aborted(self.abort_signal):
                break
            if self.max_budget_usd is not None and self.cost >= self.max_budget_usd:
                budget_exceeded = True
                break

            await self._execute_hooks("PreCompact")
            orchestrated = await self.context_orchestrator.apply_before_model_call(
                messages=self.messages,
                active_model=self.active_model,
                compact_state=self.compact_state,
                turn_count=self.turn_count,
            )
            compacted_messages = orchestrated.get("messages")
            new_state = orchestrated.get("compact_state")
            summary_text = str(orchestrated.get("summary") or "")
            if isinstance(compacted_messages, list):
                self.messages = [m for m in compacted_messages if isinstance(m, ConversationMessage)]
            if isinstance(new_state, AutoCompactState):
                self.compact_state = new_state
            if summary_text:
                yield {
                    "type": "system",
                    "subtype": "compact_boundary",
                    "summary": summary_text,
                }
            await self._execute_hooks("PostCompact")

            self.turn_count += 1
            turns_remaining -= 1
            api_messages = micro_compact_messages(normalize_messages_for_api(self.messages))

            try:
                api_start = perf_counter()
                response: ProviderResponse = await with_retry(
                    lambda: self.provider.create_message(
                        model=self.active_model,
                        max_tokens=self.max_tokens,
                        system_prompt=system_prompt,
                        messages=api_messages,
                        tools=self.tools,
                        thinking=_resolve_thinking_payload(self.thinking),
                    ),
                    abort_signal=self.abort_signal,
                )
                self.api_time_ms += int((perf_counter() - api_start) * 1000)
            except Exception as exc:
                if self.fallback_model and self.active_model != self.fallback_model:
                    self.active_model = self.fallback_model
                    self.turn_count -= 1
                    turns_remaining += 1
                    continue
                if is_prompt_too_long_error(exc) and not self.compact_state.compacted:
                    cfg = self.context_orchestrator.window_config
                    compact_result = await compact_conversation(
                        self.provider,
                        self.active_model,
                        self.messages,
                        self.compact_state,
                        keep_recent_turns=cfg.keep_recent_turns,
                        summarize_earliest_turns=cfg.summarize_earliest_turns,
                        summarizer_model=cfg.summarizer_model,
                    )
                    compacted_messages = compact_result.get("compacted_messages")
                    new_state = compact_result.get("state")
                    if isinstance(compacted_messages, list):
                        self.messages = [m for m in compacted_messages if isinstance(m, ConversationMessage)]
                    if isinstance(new_state, AutoCompactState):
                        self.compact_state = new_state
                    turns_remaining += 1
                    self.turn_count -= 1
                    continue
                yield {
                    "type": "result",
                    "subtype": "error",
                    "session_id": self.session_id,
                    "is_error": True,
                    "usage": self.usage,
                    "num_turns": self.turn_count,
                    "cost": self.cost,
                }
                return

            usage = response.usage or {}
            self.usage["input_tokens"] += int(usage.get("input_tokens", 0))
            self.usage["output_tokens"] += int(usage.get("output_tokens", 0))
            self.usage["cache_creation_input_tokens"] += int(usage.get("cache_creation_input_tokens", 0))
            self.usage["cache_read_input_tokens"] += int(usage.get("cache_read_input_tokens", 0))
            self.cost += estimate_cost(self.active_model, usage)
            per_model = self.model_usage.setdefault(
                self.active_model,
                {"input_tokens": 0, "output_tokens": 0},
            )
            per_model["input_tokens"] += int(usage.get("input_tokens", 0))
            per_model["output_tokens"] += int(usage.get("output_tokens", 0))

            blocks, tool_calls = self._normalize_provider_response(response)

            if not blocks and not tool_calls:
                errors.append(
                    "Model returned an empty response (no text and no tool calls). "
                    "Please check model/provider compatibility."
                )
                yield {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "session_id": self.session_id,
                    "is_error": True,
                    "usage": self.usage,
                    "num_turns": self.turn_count,
                    "cost": self.cost,
                    "errors": list(errors),
                }
                return

            if blocks:
                if self.include_partial_messages:
                    for block in blocks:
                        if block.get("type") == "text":
                            yield {
                                "type": "partial_message",
                                "partial": {"type": "text", "text": str(block.get("text") or "")},
                            }
                        elif block.get("type") == "tool_use":
                            yield {
                                "type": "partial_message",
                                "partial": {
                                    "type": "tool_use",
                                    "name": str(block.get("name") or ""),
                                    "input": str(block.get("input") or ""),
                                },
                            }
                self.messages.append(ConversationMessage(role="assistant", content=blocks))
                yield {
                    "type": "assistant",
                    "session_id": self.session_id,
                    "message": {"role": "assistant", "content": blocks},
                }

            if response.stop_reason == "max_tokens" and max_output_recovery_attempts < max_output_recovery:
                max_output_recovery_attempts += 1
                self.messages.append(
                    ConversationMessage(role="user", content="Please continue from where you left off.")
                )
                continue

            if not tool_calls:
                break
            max_output_recovery_attempts = 0

            tool_results: list[dict[str, Any]] = []
            pending_calls: list[tuple[ProviderToolCall, ToolDefinition | None, PolicyDecision]] = []
            tool_context = ToolContext(
                cwd=self.cwd,
                abort_signal=self.abort_signal,
                provider=self.provider,
                model=self.model,
                api_type=getattr(self.provider, "api_type", None),
                state=self.tool_context.state,
            )
            for raw_call in tool_calls:
                tool = self._tool_map.get(raw_call.name)
                decision = evaluate_tool_policy(raw_call.name, raw_call.input, self.cwd)
                if decision.action == "deny":
                    tool_results.append(self._policy_denied_result(raw_call, decision.reason, decision.risk_level))
                    continue
                normalized_call = ProviderToolCall(
                    id=raw_call.id,
                    name=raw_call.name,
                    input=decision.normalized_input,
                )
                pending_calls.append((normalized_call, tool, decision))

            for call, tool, policy_decision in pending_calls:
                requires_policy_permission = policy_decision.action == "require_permission"
                requires_tool_permission = bool(tool and self._requires_permission(tool))
                needs_permission = requires_policy_permission or requires_tool_permission

                if needs_permission:
                    if self.wait_for_permission_decision is None:
                        reason = (
                            f"Policy requires approval: {policy_decision.reason}"
                            if requires_policy_permission
                            else f'Permission denied for tool "{call.name}"'
                        )
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "result": {
                                    "tool_use_id": call.id,
                                    "tool_name": call.name,
                                    "output": reason,
                                    "is_error": True,
                                },
                            }
                        )
                        continue

                    permission_id = str(uuid.uuid4())
                    permission_payload = {
                        "id": permission_id,
                        "toolUseId": call.id,
                        "toolName": call.name,
                        "input": call.input,
                        "message": (
                            f"Allow tool '{call.name}'? (risk={policy_decision.risk_level}, reason={policy_decision.reason})"
                            if requires_policy_permission
                            else f"Allow tool '{call.name}'?"
                        ),
                    }
                    await self._execute_hooks("PermissionRequest", permission_payload)
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
                        approved = await asyncio.wait_for(self.wait_for_permission_decision(permission_id), timeout=600)
                    except Exception:
                        approved = False
                    if not approved:
                        await self._execute_hooks(
                            "PermissionDenied",
                            {"toolName": call.name, "toolInput": call.input, "toolUseId": call.id},
                        )
                        tool_results.append(
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

                if tool is None:
                    tool_results.append(await self._execute_single_tool(call, None, tool_context))
                    continue

                if self.can_use_tool:
                    try:
                        permission = await self.can_use_tool(tool, call.input)
                    except Exception as exc:
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "result": {
                                    "tool_use_id": call.id,
                                    "tool_name": call.name,
                                    "output": f"Permission check error: {exc}",
                                    "is_error": True,
                                },
                            }
                        )
                        continue
                    if str(permission.get("behavior", "allow")) == "deny":
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "result": {
                                    "tool_use_id": call.id,
                                    "tool_name": call.name,
                                    "output": permission.get("message") or f'Permission denied for tool "{call.name}"',
                                    "is_error": True,
                                },
                            }
                        )
                        continue
                    updated_input = None
                    if "updatedInput" in permission:
                        updated_input = permission.get("updatedInput")
                    elif "updated_input" in permission:
                        updated_input = permission.get("updated_input")
                    if updated_input is not None:
                        call = ProviderToolCall(id=call.id, name=call.name, input=updated_input)

                tool_results.append(await self._execute_single_tool(call, tool, tool_context))

            tool_result_blocks: list[dict[str, Any]] = []
            for result in tool_results:
                yield result
                payload = result.get("result", {})
                output = str(payload.get("output", ""))
                is_error = bool(payload.get("is_error"))
                if is_error:
                    errors.append(output)
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": payload.get("tool_use_id"),
                        "content": output,
                        "is_error": is_error,
                    }
                )

            if tool_result_blocks:
                self.messages.append(ConversationMessage(role="user", content=tool_result_blocks))

            if response.stop_reason == "end_turn":
                break

        await self._execute_hooks("Stop")
        await self._execute_hooks("SessionEnd")

        subtype = "success"
        if budget_exceeded:
            subtype = "error_max_budget_usd"
        elif turns_remaining <= 0:
            subtype = "error_max_turns"

        structured_output = None
        if self.json_schema or self.output_format:
            structured_output = _try_parse_structured_output(_extract_last_assistant_text(self.messages))

        yield {
            "type": "result",
            "subtype": subtype,
            "session_id": self.session_id,
            "is_error": subtype != "success",
            "num_turns": self.turn_count,
            "total_cost_usd": self.cost,
            "duration_api_ms": self.api_time_ms,
            "usage": self.usage,
            "model_usage": self.model_usage
            if self.model_usage
            else {
                self.active_model: {
                    "input_tokens": self.usage["input_tokens"],
                    "output_tokens": self.usage["output_tokens"],
                }
            },
            "cost": self.cost,
            "structured_output": structured_output,
            "errors": errors if errors else None,
        }

    async def submitMessage(self, prompt: str | list[dict[str, Any]]) -> AsyncGenerator[SDKMessage, None]:
        async for item in self.submit_message(prompt):
            yield item

    def get_messages(self) -> list[ConversationMessage]:
        return list(self.messages)

    def getMessages(self) -> list[ConversationMessage]:
        return self.get_messages()

    def get_usage(self) -> dict[str, int]:
        return dict(self.usage)

    def getUsage(self) -> dict[str, int]:
        return self.get_usage()

    def get_cost(self) -> float:
        return float(self.cost)

    def getCost(self) -> float:
        return self.get_cost()

    def get_context_metadata(self) -> dict[str, Any]:
        return self.context_orchestrator.export_metadata()

    def getContextMetadata(self) -> dict[str, Any]:
        return self.get_context_metadata()
