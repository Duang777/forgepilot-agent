from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

MessageRole = Literal["user", "assistant", "system", "tool"]
ApiType = Literal["anthropic-messages", "openai-completions"]
PermissionMode = Literal[
    "default",
    "acceptEdits",
    "bypassPermissions",
    "plan",
    "dontAsk",
    "auto",
]


@dataclass(slots=True)
class ConversationMessage:
    role: MessageRole
    content: Any


@dataclass(slots=True)
class ToolContext:
    cwd: Path
    state: dict[str, Any] = field(default_factory=dict)
    abort_signal: Any | None = None
    provider: Any | None = None
    model: str | None = None
    api_type: ApiType | None = None


@dataclass(slots=True)
class ToolResult:
    content: str | list[dict[str, Any]]
    is_error: bool = False


ToolCallable = Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult]]


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    call: ToolCallable
    read_only: bool = False
    concurrency_safe: bool = True


@dataclass(slots=True)
class ThinkingConfig:
    type: Literal["adaptive", "enabled", "disabled"] = "adaptive"
    budget_tokens: int | None = None


@dataclass(slots=True)
class AgentOptions:
    model: str = "claude-sonnet-4-20250514"
    api_type: ApiType = "anthropic-messages"
    api_key: str | None = None
    base_url: str | None = None
    cwd: str | None = None
    system_prompt: str | None = None
    append_system_prompt: str | None = None
    tools: list[ToolDefinition] | None = None
    max_turns: int = 20
    max_budget_usd: float | None = None
    thinking: ThinkingConfig = field(default_factory=ThinkingConfig)
    permission_mode: PermissionMode = "bypassPermissions"
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    on_permission_request: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    wait_for_permission_decision: Callable[[str], Awaitable[bool]] | None = None
    session_id: str | None = None
    persist_session: bool = True
    mcp_servers: dict[str, dict[str, Any]] | None = None
    env: dict[str, str | None] | None = None
    agents: dict[str, dict[str, Any]] | None = None
    skills_paths: list[str] | None = None


@dataclass(slots=True)
class QueryRequest:
    prompt: str
    options: AgentOptions = field(default_factory=AgentOptions)


@dataclass(slots=True)
class QueryResult:
    text: str
    num_turns: int
    usage: dict[str, int]
    cost: float
    session_id: str


SDKMessage = dict[str, Any]
