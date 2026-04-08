from __future__ import annotations

import uuid
from typing import Any

import httpx

from forgepilot_sdk.providers.base import LLMProvider, ProviderResponse, ProviderToolCall
from forgepilot_sdk.types import ConversationMessage, ToolDefinition


def _to_anthropic_tool(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }


def _to_anthropic_message(message: ConversationMessage) -> dict[str, Any]:
    if message.role == "tool":
        payload = message.content if isinstance(message.content, dict) else {}
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": payload.get("tool_call_id", ""),
                    "content": payload.get("content", ""),
                    "is_error": payload.get("is_error", False),
                }
            ],
        }

    if message.role == "assistant" and isinstance(message.content, dict):
        blocks: list[dict[str, Any]] = []
        text = message.content.get("text")
        if isinstance(text, str) and text:
            blocks.append({"type": "text", "text": text})

        raw_calls = message.content.get("tool_calls")
        if isinstance(raw_calls, list):
            for raw in raw_calls:
                if not isinstance(raw, dict):
                    continue
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": raw.get("id") or str(uuid.uuid4()),
                        "name": raw.get("name") or "",
                        "input": raw.get("input") or {},
                    }
                )

        if not blocks:
            blocks = [{"type": "text", "text": ""}]
        return {"role": "assistant", "content": blocks}

    content = message.content if isinstance(message.content, str) else str(message.content)
    role = "assistant" if message.role == "assistant" else "user"
    return {"role": role, "content": [{"type": "text", "text": content}]}


class AnthropicMessagesProvider(LLMProvider):
    def __init__(self, *, api_key: str, base_url: str | None = None, timeout: float = 120.0) -> None:
        self.api_type = "anthropic-messages"
        self._api_key = api_key
        self._base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        self._timeout = timeout

    async def create_message(
        self,
        *,
        model: str,
        system_prompt: str,
        messages: list[ConversationMessage],
        tools: list[ToolDefinition],
        max_tokens: int | None = None,
    ) -> ProviderResponse:
        endpoint = f"{self._base_url}/v1/messages"
        payload: dict[str, Any] = {
            "model": model,
            "system": system_prompt,
            "messages": [_to_anthropic_message(m) for m in messages],
            "max_tokens": max_tokens or 4096,
        }
        if tools:
            payload["tools"] = [_to_anthropic_tool(t) for t in tools]

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(endpoint, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        text_parts: list[str] = []
        tool_calls: list[ProviderToolCall] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            if block.get("type") == "tool_use":
                tool_calls.append(
                    ProviderToolCall(
                        id=block.get("id") or str(uuid.uuid4()),
                        name=block.get("name", ""),
                        input=block.get("input") or {},
                    )
                )

        usage = data.get("usage") or {}
        return ProviderResponse(
            content="".join(text_parts).strip(),
            tool_calls=tool_calls,
            usage={
                "input_tokens": int(usage.get("input_tokens", 0)),
                "output_tokens": int(usage.get("output_tokens", 0)),
            },
            stop_reason=data.get("stop_reason"),
            raw=data,
        )

