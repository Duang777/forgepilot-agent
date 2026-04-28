from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

from forgepilot_sdk.engine import QueryEngine
from forgepilot_sdk.hooks import HookDefinition, HookRegistry, create_hook_registry
from forgepilot_sdk.mcp import close_all_connections, connect_mcp_server
from forgepilot_sdk.sdk_mcp_server import McpSdkServerConfig, is_sdk_server_config
from forgepilot_sdk.session import (
    fork_session,
    list_sessions,
    load_session,
    save_session,
    update_session_metadata,
)
from forgepilot_sdk.skills import init_bundled_skills, load_default_skill_registry, load_skill_registry_from_paths
from forgepilot_sdk.tools import (
    assemble_tool_pool,
    filter_tools,
    get_all_base_tools,
    register_agents,
    set_deferred_tools,
    set_mcp_connections,
)
from forgepilot_sdk.types import (
    AgentOptions,
    ConversationMessage,
    PermissionMode,
    QueryRequest,
    QueryResult,
    SDKMessage,
    ThinkingConfig,
    ToolDefinition,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _resolve_alias(value: Any, alias: Any) -> Any:
    return alias if alias is not None else value


def _first_non_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _coalesce(value: Any, default: Any) -> Any:
    return default if value is None else value


def _coalesce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return bool(value)


def _canonicalize_options(options: AgentOptions, *, fill_defaults: bool) -> AgentOptions:
    resolved_model = options.model
    resolved_api_type = _resolve_alias(options.api_type, options.apiType)
    resolved_api_key = _resolve_alias(options.api_key, options.apiKey)
    resolved_base_url = _resolve_alias(options.base_url, options.baseURL)
    resolved_system_prompt = _resolve_alias(options.system_prompt, options.systemPrompt)
    resolved_append_system_prompt = _resolve_alias(options.append_system_prompt, options.appendSystemPrompt)
    resolved_max_turns = _resolve_alias(options.max_turns, options.maxTurns)
    resolved_max_budget_usd = _resolve_alias(options.max_budget_usd, options.maxBudgetUsd)
    resolved_max_tokens = _resolve_alias(options.max_tokens, options.maxTokens)
    resolved_permission_mode = _resolve_alias(options.permission_mode, options.permissionMode)
    resolved_can_use_tool = _resolve_alias(options.can_use_tool, options.canUseTool)
    resolved_allowed_tools = _resolve_alias(options.allowed_tools, options.allowedTools)
    resolved_disallowed_tools = _resolve_alias(options.disallowed_tools, options.disallowedTools)
    resolved_session_id = _resolve_alias(options.session_id, options.sessionId)
    resolved_persist_session = _resolve_alias(options.persist_session, options.persistSession)
    resolved_partial = _resolve_alias(options.include_partial_messages, options.includePartialMessages)
    resolved_abort_signal = _first_non_none(
        _resolve_alias(options.abort_signal, options.abortSignal),
        _resolve_alias(options.abort_controller, options.abortController),
    )
    resolved_mcp_servers = _resolve_alias(options.mcp_servers, options.mcpServers)
    resolved_json_schema = _resolve_alias(options.json_schema, options.jsonSchema)
    resolved_output_format = _resolve_alias(options.output_format, options.outputFormat)
    resolved_continue_session = _first_non_none(
        options.continue_session,
        options.continueSession,
        options.continue_,
    )
    resolved_fork_session = _resolve_alias(options.fork_session, options.forkSession)
    resolved_setting_sources = _resolve_alias(options.setting_sources, options.settingSources)
    resolved_additional_directories = _resolve_alias(options.additional_directories, options.additionalDirectories)
    resolved_debug_file = _resolve_alias(options.debug_file, options.debugFile)
    resolved_tool_config = _resolve_alias(options.tool_config, options.toolConfig)
    resolved_prompt_suggestions = _resolve_alias(options.prompt_suggestions, options.promptSuggestions)
    resolved_strict_mcp_config = _resolve_alias(options.strict_mcp_config, options.strictMcpConfig)
    resolved_extra_args = _resolve_alias(options.extra_args, options.extraArgs)
    resolved_permission_prompt_tool_name = _resolve_alias(
        options.permission_prompt_tool_name,
        options.permissionPromptToolName,
    )
    resolved_fallback_model = _resolve_alias(options.fallback_model, options.fallbackModel)
    resolved_max_thinking_tokens = _resolve_alias(options.max_thinking_tokens, options.maxThinkingTokens)

    resolved_thinking = options.thinking
    if resolved_thinking is None and resolved_max_thinking_tokens is not None:
        resolved_thinking = ThinkingConfig(
            type="enabled",
            budget_tokens=int(resolved_max_thinking_tokens),
            budgetTokens=int(resolved_max_thinking_tokens),
        )

    if fill_defaults:
        resolved_model = _coalesce(resolved_model, "claude-sonnet-4-6")
        resolved_max_turns = int(_coalesce(resolved_max_turns, 20))
        resolved_max_tokens = int(_coalesce(resolved_max_tokens, 16384))
        resolved_permission_mode = _coalesce(resolved_permission_mode, "bypassPermissions")
        resolved_persist_session = _coalesce_bool(resolved_persist_session, True)
        resolved_partial = _coalesce_bool(resolved_partial, False)
        resolved_thinking = _coalesce(resolved_thinking, ThinkingConfig())

    return replace(
        options,
        model=resolved_model,
        api_type=resolved_api_type,
        api_key=resolved_api_key,
        base_url=resolved_base_url,
        system_prompt=resolved_system_prompt,
        append_system_prompt=resolved_append_system_prompt,
        max_turns=int(resolved_max_turns) if resolved_max_turns is not None else None,
        max_budget_usd=resolved_max_budget_usd,
        max_tokens=int(resolved_max_tokens) if resolved_max_tokens is not None else None,
        thinking=resolved_thinking,
        permission_mode=resolved_permission_mode,
        can_use_tool=resolved_can_use_tool,
        allowed_tools=resolved_allowed_tools,
        disallowed_tools=resolved_disallowed_tools,
        session_id=resolved_session_id,
        persist_session=resolved_persist_session,
        include_partial_messages=resolved_partial,
        abort_signal=resolved_abort_signal,
        mcp_servers=resolved_mcp_servers,
        json_schema=resolved_json_schema,
        output_format=resolved_output_format,
        continue_session=resolved_continue_session,
        fork_session=resolved_fork_session,
        setting_sources=resolved_setting_sources,
        additional_directories=resolved_additional_directories,
        debug_file=resolved_debug_file,
        tool_config=resolved_tool_config,
        prompt_suggestions=resolved_prompt_suggestions,
        strict_mcp_config=resolved_strict_mcp_config,
        extra_args=resolved_extra_args,
        permission_prompt_tool_name=resolved_permission_prompt_tool_name,
        fallback_model=resolved_fallback_model,
        max_thinking_tokens=resolved_max_thinking_tokens,
    )


def _merge_options(base: AgentOptions, overrides: AgentOptions) -> AgentOptions:
    merged = base
    for f in fields(AgentOptions):
        name = f.name
        value = getattr(overrides, name)
        if value is None:
            continue
        merged = replace(merged, **{name: value})
    return merged


def _resolve_api_type(options: AgentOptions) -> str:
    if options.api_type:
        return options.api_type
    env_api_type = (options.env or {}).get("CODEANY_API_TYPE") or os.getenv("CODEANY_API_TYPE")
    if env_api_type in {"anthropic-messages", "openai-completions"}:
        return env_api_type
    model = (options.model or "").lower()
    if any(prefix in model for prefix in ["gpt", "o1", "o3", "o4", "deepseek", "qwen", "glm", "mistral", "gemma"]):
        return "openai-completions"
    return "anthropic-messages"


def _pick_api_key(options: AgentOptions) -> str:
    return (
        options.api_key
        or (options.env or {}).get("CODEANY_API_KEY")
        or (options.env or {}).get("CODEANY_AUTH_TOKEN")
        or os.getenv("CODEANY_API_KEY")
        or os.getenv("CODEANY_AUTH_TOKEN")
        or ""
    )


def _pick_base_url(options: AgentOptions) -> str | None:
    return options.base_url or (options.env or {}).get("CODEANY_BASE_URL") or os.getenv("CODEANY_BASE_URL")


def _resolve_system_prompt_options(
    system_prompt: Any,
    append_system_prompt: str | None,
) -> tuple[str | None, str | None]:
    if isinstance(system_prompt, dict) and str(system_prompt.get("type")) == "preset":
        append = system_prompt.get("append")
        merged_append = append_system_prompt or ""
        if isinstance(append, str) and append.strip():
            merged_append = f"{merged_append}\n{append}".strip()
        return None, merged_append or None
    return system_prompt if isinstance(system_prompt, str) else None, append_system_prompt


def _to_message_log_entry(message_type: str, message: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": message_type,
        "message": message,
        "uuid": str(uuid.uuid4()),
        "timestamp": _utc_now_iso(),
    }


def _build_hook_registry(hooks_config: dict[str, list[dict[str, Any]]] | None) -> HookRegistry | None:
    if not hooks_config:
        return None
    registry = create_hook_registry()
    registered = False

    for event, definitions in hooks_config.items():
        if not isinstance(definitions, list):
            continue
        for definition in definitions:
            if not isinstance(definition, dict):
                continue
            handlers = definition.get("hooks")
            if isinstance(handlers, list):
                for handler in handlers:
                    if not callable(handler):
                        continue

                    async def _adapter(
                        payload: dict[str, Any],
                        _handler: Any = handler,
                    ) -> dict[str, Any] | None:
                        result = _handler(
                            payload,
                            str(payload.get("toolUseId") or ""),
                            {"signal": payload.get("abortSignal")},
                        )
                        if hasattr(result, "__await__"):
                            result = await result
                        return result if isinstance(result, dict) else None

                    registry.register(
                        event,  # type: ignore[arg-type]
                        HookDefinition(
                            handler=_adapter,
                            matcher=definition.get("matcher"),
                            timeout=definition.get("timeout"),
                        ),
                    )
                    registered = True
                continue

            registry.register(
                event,  # type: ignore[arg-type]
                HookDefinition(
                    command=definition.get("command"),
                    handler=definition.get("handler"),
                    matcher=definition.get("matcher"),
                    timeout=definition.get("timeout"),
                ),
            )
            registered = True

    return registry if registered else None


class Agent:
    def __init__(self, options: AgentOptions | None = None) -> None:
        from forgepilot_sdk.providers import create_provider

        self.cfg = _canonicalize_options(options or AgentOptions(), fill_defaults=True)
        self.cfg = replace(
            self.cfg,
            api_type=_resolve_api_type(self.cfg),  # type: ignore[arg-type]
            api_key=_pick_api_key(self.cfg),
            base_url=_pick_base_url(self.cfg),
            cwd=self.cfg.cwd or str(Path.cwd()),
        )
        self.api_credentials = {"key": self.cfg.api_key, "base_url": self.cfg.base_url}
        self.model_id = self.cfg.model or "claude-sonnet-4-6"
        self.api_type = self.cfg.api_type or _resolve_api_type(self.cfg)
        self.provider = create_provider(
            self.api_type,  # type: ignore[arg-type]
            api_key=self.api_credentials["key"] or "",
            base_url=self.api_credentials["base_url"],
        )

        self.base_tools = self._build_base_tools(self.cfg.tools)
        self.tool_pool = assemble_tool_pool(
            base_tools=self.base_tools,
            extra_tools=[],
            allowed_tools=self.cfg.allowed_tools,
            disallowed_tools=self.cfg.disallowed_tools,
        )

        if self.cfg.skills_paths:
            self.skill_registry = load_skill_registry_from_paths(self.cfg.skills_paths)
        else:
            self.skill_registry = load_default_skill_registry()
        init_bundled_skills()

        self.history: list[ConversationMessage] = []
        self.message_log: list[dict[str, Any]] = []
        self.sid = self.cfg.session_id or str(uuid.uuid4())
        self._mcp_connections = []
        self._mcp_servers_status: list[dict[str, str]] = []
        self._setup_completed = False
        self._session_loaded = False
        self._abort_event: asyncio.Event | None = None
        self._active_abort_signal: Any | None = None
        self.hook_registry = _build_hook_registry(self.cfg.hooks)

    def _build_base_tools(
        self,
        tools_option: list[ToolDefinition] | list[str] | dict[str, Any] | None,
    ) -> list[ToolDefinition]:
        all_base = get_all_base_tools()
        if isinstance(tools_option, dict) and str(tools_option.get("type")) == "preset":
            return all_base
        if tools_option is None:
            return all_base
        if isinstance(tools_option, list) and tools_option and isinstance(tools_option[0], str):
            return filter_tools(all_base, allowed_tools=[str(x) for x in tools_option])
        if isinstance(tools_option, list):
            return [t for t in tools_option if isinstance(t, ToolDefinition)]
        return all_base

    def _choose_continue_session_id(self, cwd: str) -> str | None:
        sessions = list_sessions()
        for metadata in sessions:
            if str(metadata.get("cwd") or "") == cwd:
                sid = str(metadata.get("id") or "")
                if sid:
                    return sid
        return None

    def _restore_from_session(self, session_id: str) -> None:
        data = load_session(session_id)
        if not data:
            return
        restored: list[ConversationMessage] = []
        for msg in data.get("messages", []):
            if not isinstance(msg, dict):
                continue
            restored.append(
                ConversationMessage(
                    role=msg.get("role", "user"),
                    content=msg.get("content"),
                )
            )
        self.history = restored
        self.sid = session_id

    async def _setup(self) -> None:
        if self._setup_completed:
            return

        if self.cfg.agents:
            register_agents(self.cfg.agents)

        extra_tools = []
        self._mcp_connections = []
        self._mcp_servers_status = []

        if self.cfg.mcp_servers:
            for name, config in self.cfg.mcp_servers.items():
                if is_sdk_server_config(config):
                    tools = []
                    if isinstance(config, McpSdkServerConfig):
                        tools = config.tools
                    elif isinstance(config, dict):
                        raw_tools = config.get("tools")
                        tools = [t for t in (raw_tools if isinstance(raw_tools, list) else []) if isinstance(t, ToolDefinition)]
                    if tools:
                        extra_tools.extend(tools)
                    self._mcp_servers_status.append({"name": name, "status": "connected"})
                    continue
                connection = await connect_mcp_server(name, config)
                self._mcp_connections.append(connection)
                self._mcp_servers_status.append({"name": connection.name, "status": connection.status})
                if connection.status == "connected" and connection.tools:
                    extra_tools.extend(connection.tools)

        self.tool_pool = assemble_tool_pool(
            base_tools=self.base_tools,
            extra_tools=extra_tools,
            allowed_tools=self.cfg.allowed_tools,
            disallowed_tools=self.cfg.disallowed_tools,
        )

        set_mcp_connections(self._mcp_connections)
        active_names = {tool.name for tool in self.tool_pool}
        deferred = [tool for tool in get_all_base_tools() if tool.name not in active_names]
        set_deferred_tools(deferred)

        if not self._session_loaded:
            restore_session_id: str | None = None
            if self.cfg.resume:
                restore_session_id = self.cfg.resume
            elif self.cfg.session_id:
                restore_session_id = self.cfg.session_id
            elif self.cfg.continue_session:
                restore_session_id = self._choose_continue_session_id(str(self.cfg.cwd or Path.cwd()))

            if restore_session_id:
                if self.cfg.fork_session:
                    forked = fork_session(restore_session_id)
                    if forked:
                        restore_session_id = forked
                self._restore_from_session(restore_session_id)
            self._session_loaded = True

        self._setup_completed = True

    async def query(self, prompt: str, overrides: AgentOptions | None = None) -> AsyncGenerator[SDKMessage, None]:
        await self._setup()
        raw_overrides = _canonicalize_options(overrides, fill_defaults=False) if overrides else None
        opts = self.cfg if raw_overrides is None else _merge_options(self.cfg, raw_overrides)
        cwd = str(opts.cwd or Path.cwd())
        if opts.abort_signal is None:
            self._abort_event = asyncio.Event()
            opts = replace(opts, abort_signal=self._abort_event)
            self._active_abort_signal = self._abort_event
        else:
            self._abort_event = None
            self._active_abort_signal = opts.abort_signal

        if raw_overrides and raw_overrides.continue_session and not (raw_overrides.session_id or raw_overrides.resume):
            continue_sid = self._choose_continue_session_id(cwd)
            if continue_sid:
                opts = replace(opts, session_id=continue_sid)

        local_sid = opts.session_id or opts.resume or self.sid
        if raw_overrides and raw_overrides.fork_session and local_sid:
            forked_sid = fork_session(local_sid)
            if forked_sid:
                local_sid = forked_sid

        provider = self.provider
        if (
            opts.api_type != self.api_type
            or opts.api_key != self.api_credentials["key"]
            or opts.base_url != self.api_credentials["base_url"]
        ):
            from forgepilot_sdk.providers import create_provider

            provider = create_provider(
                opts.api_type or self.api_type,  # type: ignore[arg-type]
                api_key=opts.api_key or "",
                base_url=opts.base_url,
            )

        active_tools = list(self.tool_pool)
        if raw_overrides and raw_overrides.tools is not None:
            if isinstance(raw_overrides.tools, list) and raw_overrides.tools and isinstance(raw_overrides.tools[0], str):
                active_tools = filter_tools(self.tool_pool, allowed_tools=[str(x) for x in raw_overrides.tools])
            elif isinstance(raw_overrides.tools, list):
                active_tools = [t for t in raw_overrides.tools if isinstance(t, ToolDefinition)]

        if raw_overrides and (raw_overrides.allowed_tools is not None or raw_overrides.disallowed_tools is not None):
            active_tools = filter_tools(
                active_tools,
                allowed_tools=raw_overrides.allowed_tools,
                disallowed_tools=raw_overrides.disallowed_tools,
            )

        resolved_system_prompt, resolved_append_prompt = _resolve_system_prompt_options(
            opts.system_prompt,
            opts.append_system_prompt,
        )

        can_use_tool = opts.can_use_tool
        if can_use_tool is None:
            async def _default_can_use_tool(_tool: ToolDefinition, _input: Any) -> dict[str, Any]:
                return {"behavior": "allow"}

            can_use_tool = _default_can_use_tool

        query_hook_registry = self.hook_registry
        if raw_overrides and raw_overrides.hooks:
            query_hook_registry = _build_hook_registry(raw_overrides.hooks)

        engine = QueryEngine(
            provider=provider,
            model=opts.model or self.model_id,
            tools=active_tools,
            cwd=Path(cwd).resolve(),
            max_turns=int(opts.max_turns or 20),
            max_budget_usd=opts.max_budget_usd,
            max_tokens=int(opts.max_tokens or 16384),
            thinking=opts.thinking,
            system_prompt=resolved_system_prompt,
            append_system_prompt=resolved_append_prompt,
            session_id=local_sid,
            skill_registry=self.skill_registry,
            mcp_servers=self._mcp_servers_status,
            permission_mode=opts.permission_mode or "bypassPermissions",
            can_use_tool=can_use_tool,
            include_partial_messages=bool(opts.include_partial_messages),
            abort_signal=opts.abort_signal,
            on_permission_request=opts.on_permission_request,
            wait_for_permission_decision=opts.wait_for_permission_decision,
            hook_registry=query_hook_registry,
            json_schema=opts.json_schema,
            output_format=opts.output_format,
            fallback_model=opts.fallback_model,
            effort=opts.effort,
            agents=opts.agents,
        )
        engine.tool_context.state["api_key"] = opts.api_key
        engine.tool_context.state["base_url"] = opts.base_url
        engine.tool_context.state["default_model"] = opts.model or self.model_id
        engine.tool_context.state["effort"] = opts.effort
        engine.tool_context.state["sandbox"] = opts.sandbox
        engine.tool_context.state["tool_config"] = opts.tool_config

        preload_messages = list(self.history)
        if local_sid != self.sid or (raw_overrides and (raw_overrides.session_id or raw_overrides.resume or raw_overrides.continue_session)):
            restored = load_session(local_sid)
            if restored:
                preload_messages = [
                    ConversationMessage(role=msg.get("role", "user"), content=msg.get("content"))
                    for msg in restored.get("messages", [])
                    if isinstance(msg, dict)
                ]
        engine.messages.extend(preload_messages)

        async for event in engine.submit_message(prompt):
            yield event
            if event.get("type") == "assistant":
                self.message_log.append(
                    {
                        **_to_message_log_entry(
                            "assistant",
                            event.get("message", {"role": "assistant", "content": []}),
                        ),
                        "usage": event.get("usage"),
                        "cost": event.get("cost"),
                    }
                )

        if hasattr(engine, "get_messages") and callable(getattr(engine, "get_messages")):
            self.history = list(getattr(engine, "get_messages")())
        else:
            self.history = list(getattr(engine, "messages", []))
        self.sid = engine.session_id
        self.message_log.append(_to_message_log_entry("user", {"role": "user", "content": prompt}))

        if opts.persist_session is False:
            return
        # Keep best-effort immediate save for compatibility with existing API behavior.
        save_session(self.sid, self.history, {"cwd": cwd, "model": opts.model or self.model_id})
        try:
            update_session_metadata(
                self.sid,
                {"contextCompaction": engine.get_context_metadata()},
            )
        except Exception:
            pass

    async def prompt(self, prompt: str, overrides: AgentOptions | None = None) -> QueryResult:
        started = time.perf_counter()
        collected = {"text": "", "turns": 0, "usage": {"input_tokens": 0, "output_tokens": 0}, "cost": 0.0}

        for event in [e async for e in self.query(prompt, overrides)]:
            if event.get("type") == "assistant":
                fragments = [
                    block.get("text", "")
                    for block in event.get("message", {}).get("content", [])
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                if fragments:
                    collected["text"] = "".join([str(x) for x in fragments if x])
            elif event.get("type") == "result":
                collected["turns"] = int(event.get("num_turns", 0))
                collected["usage"] = event.get("usage", collected["usage"])
                collected["cost"] = float(event.get("total_cost_usd", event.get("cost", 0.0)))

        return QueryResult(
            text=str(collected["text"]).strip(),
            num_turns=int(collected["turns"]),
            usage=dict(collected["usage"]),
            cost=float(collected["cost"]),
            session_id=self.sid,
            duration_ms=int((time.perf_counter() - started) * 1000),
            messages=list(self.message_log),
        )

    def get_messages(self) -> list[dict[str, Any]]:
        return list(self.message_log)

    def getMessages(self) -> list[dict[str, Any]]:
        return self.get_messages()

    def clear(self) -> None:
        self.history = []
        self.message_log = []

    async def interrupt(self) -> None:
        if self._abort_event is not None:
            self._abort_event.set()
        signal = self._active_abort_signal
        if signal is not None and hasattr(signal, "abort"):
            try:
                signal.abort()
            except Exception:
                pass

    async def set_model(self, model: str | None) -> None:
        if model:
            self.model_id = model
            self.cfg = replace(self.cfg, model=model)

    async def setModel(self, model: str | None) -> None:
        await self.set_model(model)

    async def set_permission_mode(self, mode: PermissionMode) -> None:
        self.cfg = replace(self.cfg, permission_mode=mode, permissionMode=mode)

    async def setPermissionMode(self, mode: PermissionMode) -> None:
        await self.set_permission_mode(mode)

    async def set_max_thinking_tokens(self, max_thinking_tokens: int | None) -> None:
        if max_thinking_tokens is None:
            self.cfg = replace(self.cfg, thinking=ThinkingConfig(type="disabled"))
            return
        self.cfg = replace(
            self.cfg,
            thinking=ThinkingConfig(
                type="enabled",
                budget_tokens=int(max_thinking_tokens),
                budgetTokens=int(max_thinking_tokens),
            ),
        )

    async def setMaxThinkingTokens(self, max_thinking_tokens: int | None) -> None:
        await self.set_max_thinking_tokens(max_thinking_tokens)

    def get_session_id(self) -> str:
        return self.sid

    def getSessionId(self) -> str:
        return self.get_session_id()

    def get_api_type(self) -> str:
        return self.api_type

    def getApiType(self) -> str:
        return self.get_api_type()

    async def stop_task(self, task_id: str) -> None:
        from forgepilot_sdk.tools import get_task

        task = get_task(task_id)
        if isinstance(task, dict):
            task["status"] = "cancelled"

    async def stopTask(self, task_id: str) -> None:
        await self.stop_task(task_id)

    async def close(self) -> None:
        if self.cfg.persist_session is not False and self.history:
            try:
                save_session(
                    self.sid,
                    self.history,
                    {
                        "cwd": self.cfg.cwd or str(Path.cwd()),
                        "model": self.model_id,
                        "summary": None,
                    },
                )
            except Exception:
                pass

        if self._mcp_connections:
            await close_all_connections(self._mcp_connections)
            self._mcp_connections = []
        self._setup_completed = False


def create_agent(options: AgentOptions | None = None) -> Agent:
    return Agent(options)


def createAgent(options: AgentOptions | None = None) -> Agent:
    return create_agent(options)


def _options_from_dict(raw: dict[str, Any]) -> AgentOptions:
    payload = dict(raw)
    if "continue" in payload and "continue_" not in payload and "continueSession" not in payload:
        payload["continue_"] = payload.pop("continue")
    return AgentOptions(**payload)


async def query(request: QueryRequest | dict[str, Any]) -> AsyncGenerator[SDKMessage, None]:
    if isinstance(request, dict):
        prompt = request.get("prompt", "")
        opts = request.get("options", AgentOptions())
        if isinstance(opts, dict):
            options = _options_from_dict(opts)
        else:
            options = opts
        req = QueryRequest(prompt=prompt, options=options)
    else:
        req = request

    agent = create_agent(req.options)
    try:
        async for item in agent.query(req.prompt):
            yield item
    finally:
        await agent.close()
