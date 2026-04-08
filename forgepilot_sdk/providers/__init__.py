from __future__ import annotations

from forgepilot_sdk.providers.anthropic_messages import AnthropicMessagesProvider
from forgepilot_sdk.providers.base import LLMProvider, ProviderResponse, ProviderToolCall
from forgepilot_sdk.providers.openai_compatible import OpenAICompatibleProvider
from forgepilot_sdk.types import ApiType


def create_provider(api_type: ApiType, *, api_key: str, base_url: str | None = None) -> LLMProvider:
    if api_type == "openai-completions":
        return OpenAICompatibleProvider(api_key=api_key, base_url=base_url)
    return AnthropicMessagesProvider(api_key=api_key, base_url=base_url)


__all__ = [
    "LLMProvider",
    "ProviderResponse",
    "ProviderToolCall",
    "AnthropicMessagesProvider",
    "OpenAICompatibleProvider",
    "create_provider",
]


