from __future__ import annotations

import math
from typing import Any


def estimate_tokens(text: str) -> int:
    return max(1, int(math.ceil(len(text) / 4)))


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
            continue
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if isinstance(block.get("text"), str):
                        total += estimate_tokens(str(block.get("text")))
                    elif isinstance(block.get("content"), str):
                        total += estimate_tokens(str(block.get("content")))
                    else:
                        total += estimate_tokens(str(block))
                else:
                    total += estimate_tokens(str(block))
    return total


def estimate_system_prompt_tokens(system_prompt: str) -> int:
    return estimate_tokens(system_prompt)


def get_token_count_from_usage(usage: dict[str, int]) -> int:
    return (
        int(usage.get("input_tokens", 0))
        + int(usage.get("output_tokens", 0))
        + int(usage.get("cache_creation_input_tokens", 0))
        + int(usage.get("cache_read_input_tokens", 0))
    )


def get_context_window_size(model: str) -> int:
    lower = model.lower()
    if "opus-4" in lower and "1m" in lower:
        return 1_000_000
    if "opus-4" in lower:
        return 200_000
    if "sonnet-4" in lower:
        return 200_000
    if "haiku-4" in lower:
        return 200_000
    if "claude-3" in lower:
        return 200_000
    if "gpt-4o" in lower:
        return 128_000
    if "gpt-4-turbo" in lower:
        return 128_000
    if "gpt-4-1" in lower:
        return 1_000_000
    if "gpt-4" in lower:
        return 128_000
    if "gpt-3.5" in lower:
        return 16_385
    if "o1" in lower or "o3" in lower or "o4" in lower:
        return 200_000
    if "deepseek" in lower:
        return 128_000
    return 200_000


AUTOCOMPACT_BUFFER_TOKENS = 13_000


def get_auto_compact_threshold(model: str) -> int:
    return get_context_window_size(model) - AUTOCOMPACT_BUFFER_TOKENS


MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-6": {"input": 15 / 1_000_000, "output": 75 / 1_000_000},
    "claude-opus-4-5": {"input": 15 / 1_000_000, "output": 75 / 1_000_000},
    "claude-sonnet-4-6": {"input": 3 / 1_000_000, "output": 15 / 1_000_000},
    "claude-sonnet-4-5": {"input": 3 / 1_000_000, "output": 15 / 1_000_000},
    "claude-haiku-4-5": {"input": 0.8 / 1_000_000, "output": 4 / 1_000_000},
    "claude-3-5-sonnet": {"input": 3 / 1_000_000, "output": 15 / 1_000_000},
    "claude-3-5-haiku": {"input": 0.8 / 1_000_000, "output": 4 / 1_000_000},
    "claude-3-opus": {"input": 15 / 1_000_000, "output": 75 / 1_000_000},
    "gpt-4o": {"input": 2.5 / 1_000_000, "output": 10 / 1_000_000},
    "gpt-4o-mini": {"input": 0.15 / 1_000_000, "output": 0.6 / 1_000_000},
    "gpt-4-turbo": {"input": 10 / 1_000_000, "output": 30 / 1_000_000},
    "gpt-4-1": {"input": 2 / 1_000_000, "output": 8 / 1_000_000},
    "o1": {"input": 15 / 1_000_000, "output": 60 / 1_000_000},
    "o3": {"input": 10 / 1_000_000, "output": 40 / 1_000_000},
    "o4-mini": {"input": 1.1 / 1_000_000, "output": 4.4 / 1_000_000},
    "deepseek-chat": {"input": 0.27 / 1_000_000, "output": 1.1 / 1_000_000},
    "deepseek-reasoner": {"input": 0.55 / 1_000_000, "output": 2.19 / 1_000_000},
}


def estimate_cost(model: str, usage: dict[str, int]) -> float:
    lower = model.lower()
    pricing = {"input": 3 / 1_000_000, "output": 15 / 1_000_000}
    for key, value in MODEL_PRICING.items():
        if key in lower:
            pricing = value
            break
    return int(usage.get("input_tokens", 0)) * pricing["input"] + int(usage.get("output_tokens", 0)) * pricing["output"]


def estimateTokens(text: str) -> int:
    return estimate_tokens(text)


def estimateMessagesTokens(messages: list[dict[str, Any]]) -> int:
    return estimate_messages_tokens(messages)


def estimateSystemPromptTokens(systemPrompt: str) -> int:
    return estimate_system_prompt_tokens(systemPrompt)


def getTokenCountFromUsage(usage: dict[str, int]) -> int:
    return get_token_count_from_usage(usage)


def getContextWindowSize(model: str) -> int:
    return get_context_window_size(model)


def getAutoCompactThreshold(model: str) -> int:
    return get_auto_compact_threshold(model)


def estimateCost(model: str, usage: dict[str, int]) -> float:
    return estimate_cost(model, usage)
