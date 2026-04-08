from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any, AsyncGenerator

from forgepilot_sdk.engine import QueryEngine
from forgepilot_sdk.mcp import close_all_connections, connect_mcp_server
from forgepilot_sdk.session import load_session, save_session
from forgepilot_sdk.skills import load_default_skill_registry, load_skill_registry_from_paths
from forgepilot_sdk.tools import (
    assemble_tool_pool,
    get_all_base_tools,
    register_agents,
    set_deferred_tools,
    set_mcp_connections,
)
from forgepilot_sdk.types import AgentOptions, ConversationMessage, QueryRequest, QueryResult, SDKMessage


def _resolve_api_type(options: AgentOptions) -> str:
    if options.api_type:
        return options.api_type
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


class Agent:
    def __init__(self, options: AgentOptions | None = None) -> None:
        from forgepilot_sdk.providers import create_provider

        self.options = options or AgentOptions()
        self.options = replace(
            self.options,
            api_type=_resolve_api_type(self.options),  # type: ignore[arg-type]
            api_key=_pick_api_key(self.options),
            base_url=_pick_base_url(self.options),
            cwd=self.options.cwd or str(Path.cwd()),
        )
        if not self.options.api_key:
            raise ValueError("API key is required. Set CODEANY_API_KEY or pass api_key.")

        self.provider = create_provider(
            self.options.api_type,  # type: ignore[arg-type]
            api_key=self.options.api_key,
            base_url=self.options.base_url,
        )
        self.base_tools = self.options.tools if self.options.tools is not None else get_all_base_tools()
        self.tools = assemble_tool_pool(
            base_tools=self.base_tools,
            extra_tools=[],
            allowed_tools=self.options.allowed_tools,
            disallowed_tools=self.options.disallowed_tools,
        )
        if self.options.skills_paths:
            self.skill_registry = load_skill_registry_from_paths(self.options.skills_paths)
        else:
            self.skill_registry = load_default_skill_registry()
        self.messages: list[dict[str, Any]] = []
        self._mcp_connections = []
        self._mcp_servers_status: list[dict[str, str]] = []
        self._setup_completed = False

    async def _setup(self) -> None:
        if self._setup_completed:
            return

        if self.options.agents:
            register_agents(self.options.agents)

        extra_tools = []
        self._mcp_connections = []
        self._mcp_servers_status = []

        if self.options.mcp_servers:
            for name, config in self.options.mcp_servers.items():
                connection = await connect_mcp_server(name, config)
                self._mcp_connections.append(connection)
                self._mcp_servers_status.append({"name": connection.name, "status": connection.status})
                if connection.status == "connected" and connection.tools:
                    extra_tools.extend(connection.tools)

        self.tools = assemble_tool_pool(
            base_tools=self.base_tools,
            extra_tools=extra_tools,
            allowed_tools=self.options.allowed_tools,
            disallowed_tools=self.options.disallowed_tools,
        )

        set_mcp_connections(self._mcp_connections)
        active_names = {tool.name for tool in self.tools}
        deferred = [tool for tool in get_all_base_tools() if tool.name not in active_names]
        set_deferred_tools(deferred)

        self._setup_completed = True

    async def query(self, prompt: str, overrides: AgentOptions | None = None) -> AsyncGenerator[SDKMessage, None]:
        await self._setup()
        opts = self.options if overrides is None else replace(self.options, **overrides.__dict__)
        cwd = Path(opts.cwd or Path.cwd()).resolve()
        engine = QueryEngine(
            provider=self.provider,
            model=opts.model,
            tools=self.tools,
            cwd=cwd,
            max_turns=opts.max_turns,
            system_prompt=opts.system_prompt,
            append_system_prompt=opts.append_system_prompt,
            session_id=opts.session_id,
            skill_registry=self.skill_registry,
            mcp_servers=self._mcp_servers_status,
            permission_mode=opts.permission_mode,
            on_permission_request=opts.on_permission_request,
            wait_for_permission_decision=opts.wait_for_permission_decision,
        )
        engine.tool_context.state["api_key"] = opts.api_key
        engine.tool_context.state["base_url"] = opts.base_url
        engine.tool_context.state["default_model"] = opts.model

        if opts.session_id:
            previous = load_session(opts.session_id)
            if previous:
                for msg in previous.get("messages", []):
                    engine.messages.append(
                        ConversationMessage(role=msg.get("role", "user"), content=msg.get("content"))
                    )

        async for event in engine.submit_message(prompt):
            yield event

        if opts.persist_session:
            save_session(engine.session_id, engine.messages)

    async def prompt(self, prompt: str, overrides: AgentOptions | None = None) -> QueryResult:
        text_parts: list[str] = []
        num_turns = 0
        usage = {"input_tokens": 0, "output_tokens": 0}
        cost = 0.0
        session_id = self.options.session_id or ""
        async for event in self.query(prompt, overrides):
            if event.get("type") == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
            if event.get("type") == "result":
                num_turns = int(event.get("num_turns", 0))
                usage = event.get("usage", usage)
                cost = float(event.get("total_cost_usd", 0.0))
                session_id = event.get("session_id", session_id)

        return QueryResult(
            text="\n".join([p for p in text_parts if p]).strip(),
            num_turns=num_turns,
            usage=usage,
            cost=cost,
            session_id=session_id,
        )

    async def close(self) -> None:
        if self._mcp_connections:
            await close_all_connections(self._mcp_connections)
            self._mcp_connections = []
        self._setup_completed = False


def create_agent(options: AgentOptions | None = None) -> Agent:
    return Agent(options)


async def query(request: QueryRequest | dict[str, Any]) -> AsyncGenerator[SDKMessage, None]:
    if isinstance(request, dict):
        prompt = request.get("prompt", "")
        opts = request.get("options", AgentOptions())
        if isinstance(opts, dict):
            options = AgentOptions(**opts)
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

