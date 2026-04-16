from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from forgepilot_sdk.types import ConversationMessage, ToolDefinition


@dataclass(slots=True)
class ProviderToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(slots=True)
class ProviderResponse:
    content: str | list[dict[str, Any]] = ""
    tool_calls: list[ProviderToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str | None = None
    raw: dict[str, Any] | None = None


class LLMProvider(Protocol):
    api_type: str

    async def create_message(
        self,
        *,
        model: str,
        system_prompt: str,
        messages: list[ConversationMessage],
        tools: list[ToolDefinition],
        max_tokens: int | None = None,
        thinking: dict[str, Any] | None = None,
    ) -> ProviderResponse:
        ...


# Upstream-facing provider type aliases.
CreateMessageParams = dict[str, Any]
CreateMessageResponse = ProviderResponse
NormalizedMessageParam = ConversationMessage
NormalizedContentBlock = dict[str, Any]
NormalizedTool = dict[str, Any]
NormalizedResponseBlock = dict[str, Any]

