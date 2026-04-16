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
PermissionBehavior = Literal["allow", "deny"]


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
    content: Any
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
    enabled: bool = True
    prompt_fn: Callable[[ToolContext], Awaitable[str] | str] | None = None
    # Optional parity hooks (TypeScript shape compatibility)
    is_read_only_fn: Callable[[], bool] | None = None
    is_concurrency_safe_fn: Callable[[], bool] | None = None
    is_enabled_fn: Callable[[], bool] | None = None

    def is_read_only(self) -> bool:
        if self.is_read_only_fn is not None:
            try:
                return bool(self.is_read_only_fn())
            except Exception:
                return bool(self.read_only)
        return bool(self.read_only)

    def is_concurrency_safe(self) -> bool:
        if self.is_concurrency_safe_fn is not None:
            try:
                return bool(self.is_concurrency_safe_fn())
            except Exception:
                return bool(self.concurrency_safe)
        return bool(self.concurrency_safe)

    def is_enabled(self) -> bool:
        if self.is_enabled_fn is not None:
            try:
                return bool(self.is_enabled_fn())
            except Exception:
                return bool(self.enabled)
        return bool(self.enabled)

    async def prompt(self, context: ToolContext) -> str:
        if self.prompt_fn is None:
            return self.description
        try:
            value = self.prompt_fn(context)
            if hasattr(value, "__await__"):
                value = await value  # type: ignore[assignment]
            return str(value or self.description)
        except Exception:
            return self.description


@dataclass(slots=True)
class ThinkingConfig:
    type: Literal["adaptive", "enabled", "disabled"] = "adaptive"
    budget_tokens: int | None = None
    # TypeScript compatibility field name.
    budgetTokens: int | None = None

    def resolved_budget_tokens(self) -> int | None:
        return self.budgetTokens if self.budgetTokens is not None else self.budget_tokens


CanUseToolFn = Callable[[ToolDefinition, Any], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class AgentOptions:
    # Canonical runtime fields used by this Python SDK.
    model: str | None = None
    api_type: ApiType | None = None
    api_key: str | None = None
    base_url: str | None = None
    cwd: str | None = None
    system_prompt: str | dict[str, Any] | None = None
    append_system_prompt: str | None = None
    tools: list[ToolDefinition] | list[str] | dict[str, Any] | None = None
    max_turns: int | None = None
    max_budget_usd: float | None = None
    max_tokens: int | None = None
    thinking: ThinkingConfig | None = None
    permission_mode: PermissionMode | None = None
    can_use_tool: CanUseToolFn | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    on_permission_request: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    wait_for_permission_decision: Callable[[str], Awaitable[bool]] | None = None
    session_id: str | None = None
    resume: str | None = None
    persist_session: bool | None = None
    include_partial_messages: bool | None = None
    abort_signal: Any | None = None
    mcp_servers: dict[str, dict[str, Any]] | None = None
    env: dict[str, str | None] | None = None
    agents: dict[str, dict[str, Any]] | None = None
    skills_paths: list[str] | None = None
    hooks: dict[str, list[dict[str, Any]]] | None = None
    json_schema: dict[str, Any] | None = None
    output_format: dict[str, Any] | None = None
    abort_controller: Any | None = None
    max_thinking_tokens: int | None = None
    effort: Literal["low", "medium", "high", "max"] | None = None
    fallback_model: str | None = None
    continue_session: bool | None = None
    fork_session: bool | None = None
    enable_file_checkpointing: bool | None = None
    sandbox: dict[str, Any] | None = None
    setting_sources: list[str] | None = None
    plugins: list[dict[str, Any]] | None = None
    additional_directories: list[str] | None = None
    agent: str | None = None
    debug: bool | None = None
    debug_file: str | None = None
    tool_config: dict[str, Any] | None = None
    prompt_suggestions: bool | None = None
    strict_mcp_config: bool | None = None
    extra_args: dict[str, str | None] | None = None
    betas: list[str] | None = None
    permission_prompt_tool_name: str | None = None

    # TypeScript-style compatibility aliases (accepted but optional).
    apiType: ApiType | None = None
    apiKey: str | None = None
    baseURL: str | None = None
    systemPrompt: str | None = None
    appendSystemPrompt: str | None = None
    maxTurns: int | None = None
    maxBudgetUsd: float | None = None
    maxTokens: int | None = None
    permissionMode: PermissionMode | None = None
    canUseTool: CanUseToolFn | None = None
    allowedTools: list[str] | None = None
    disallowedTools: list[str] | None = None
    sessionId: str | None = None
    persistSession: bool | None = None
    includePartialMessages: bool | None = None
    abortSignal: Any | None = None
    mcpServers: dict[str, dict[str, Any]] | None = None
    jsonSchema: dict[str, Any] | None = None
    outputFormat: dict[str, Any] | None = None
    abortController: Any | None = None
    maxThinkingTokens: int | None = None
    fallbackModel: str | None = None
    continue_: bool | None = None
    continueSession: bool | None = None
    forkSession: bool | None = None
    enableFileCheckpointing: bool | None = None
    settingSources: list[str] | None = None
    additionalDirectories: list[str] | None = None
    debugFile: str | None = None
    toolConfig: dict[str, Any] | None = None
    promptSuggestions: bool | None = None
    strictMcpConfig: bool | None = None
    extraArgs: dict[str, str | None] | None = None
    permissionPromptToolName: str | None = None


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
    duration_ms: int = 0
    messages: list[dict[str, Any]] = field(default_factory=list)


SDKMessage = dict[str, Any]

# Upstream-facing type aliases (TypeScript export compatibility).
ContentBlockParam = dict[str, Any]
ContentBlock = dict[str, Any]
Message = dict[str, Any]
UserMessage = dict[str, Any]
AssistantMessage = dict[str, Any]
SDKAssistantMessage = dict[str, Any]
SDKToolResultMessage = dict[str, Any]
SDKResultMessage = dict[str, Any]
SDKPartialMessage = dict[str, Any]
TokenUsage = dict[str, int]
ToolInputSchema = dict[str, Any]
CanUseToolResult = dict[str, Any]
McpServerConfig = dict[str, Any]
McpStdioConfig = dict[str, Any]
McpSseConfig = dict[str, Any]
McpHttpConfig = dict[str, Any]
AgentDefinition = dict[str, Any]
SandboxSettings = dict[str, Any]
SandboxNetworkConfig = dict[str, Any]
SandboxFilesystemConfig = dict[str, Any]
OutputFormat = dict[str, Any]
SettingSource = Literal["user", "project", "local"]
ModelInfo = dict[str, Any]
QueryEngineConfig = dict[str, Any]
SkillDefinition = dict[str, Any]
SkillContentBlock = dict[str, Any]
SkillResult = dict[str, Any]
TaskStatus = Literal["pending", "in_progress", "completed", "cancelled"]
Task = dict[str, Any]
Team = dict[str, Any]
TodoItem = dict[str, Any]
AgentMessage = dict[str, Any]
CronJob = dict[str, Any]
SessionMetadata = dict[str, Any]
SessionData = dict[str, Any]
