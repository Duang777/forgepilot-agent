from __future__ import annotations

from forgepilot_sdk.providers.anthropic_messages import AnthropicMessagesProvider
from forgepilot_sdk.providers.base import LLMProvider, ProviderResponse, ProviderToolCall
from forgepilot_sdk.providers.openai_compatible import OpenAICompatibleProvider
from forgepilot_sdk.types import ApiType


def create_provider(api_type: ApiType, *, api_key: str, base_url: str | None = None) -> LLMProvider:
    if api_type == "openai-completions":
        return OpenAICompatibleProvider(api_key=api_key, base_url=base_url)
    if api_type == "anthropic-messages":
        return AnthropicMessagesProvider(api_key=api_key, base_url=base_url)
    raise ValueError(f"Unsupported API type: {api_type}. Use 'anthropic-messages' or 'openai-completions'.")


AnthropicProvider = AnthropicMessagesProvider
OpenAIProvider = OpenAICompatibleProvider


def createProvider(apiType: ApiType, opts: dict[str, str | None] | None = None) -> LLMProvider:
    payload = opts or {}
    return create_provider(
        apiType,
        api_key=str(payload.get("apiKey") or payload.get("api_key") or ""),
        base_url=payload.get("baseURL") or payload.get("base_url"),
    )


__all__ = [
    "LLMProvider",
    "ProviderResponse",
    "ProviderToolCall",
    "AnthropicMessagesProvider",
    "OpenAICompatibleProvider",
    "AnthropicProvider",
    "OpenAIProvider",
    "create_provider",
    "createProvider",
]


