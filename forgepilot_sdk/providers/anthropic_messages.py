from __future__ import annotations

import json
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
    if message.role == "user" and isinstance(message.content, list):
        blocks: list[dict[str, Any]] = []
        for block in message.content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text")
                if isinstance(text, str):
                    blocks.append({"type": "text", "text": text})
            elif block_type == "tool_result":
                content = block.get("content", "")
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False)
                blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.get("tool_use_id", ""),
                        "content": content,
                        "is_error": bool(block.get("is_error", False)),
                    }
                )
        if blocks:
            return {"role": "user", "content": blocks}
        return {"role": "user", "content": [{"type": "text", "text": ""}]}

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

    if message.role == "assistant":
        if isinstance(message.content, dict):
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

        if isinstance(message.content, list):
            blocks: list[dict[str, Any]] = []
            for block in message.content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "text" and isinstance(block.get("text"), str):
                    blocks.append({"type": "text", "text": block.get("text")})
                elif block_type == "tool_use":
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": block.get("id") or str(uuid.uuid4()),
                            "name": block.get("name") or "",
                            "input": block.get("input") or {},
                        }
                    )
            if blocks:
                return {"role": "assistant", "content": blocks}
            return {"role": "assistant", "content": [{"type": "text", "text": ""}]}

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
        thinking: dict[str, Any] | None = None,
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
        if thinking:
            payload["thinking"] = thinking

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(endpoint, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        content_blocks: list[dict[str, Any]] = []
        tool_calls: list[ProviderToolCall] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                content_blocks.append({"type": "text", "text": block.get("text", "")})
            if block.get("type") == "tool_use":
                tool_id = block.get("id") or str(uuid.uuid4())
                tool_name = block.get("name", "")
                tool_input = block.get("input") or {}
                tool_calls.append(
                    ProviderToolCall(
                        id=tool_id,
                        name=tool_name,
                        input=tool_input,
                    )
                )
                content_blocks.append({"type": "tool_use", "id": tool_id, "name": tool_name, "input": tool_input})

        usage = data.get("usage") or {}
        return ProviderResponse(
            content=content_blocks if content_blocks else "",
            tool_calls=tool_calls,
            usage={
                "input_tokens": int(usage.get("input_tokens", 0)),
                "output_tokens": int(usage.get("output_tokens", 0)),
                "cache_creation_input_tokens": int(usage.get("cache_creation_input_tokens", 0)),
                "cache_read_input_tokens": int(usage.get("cache_read_input_tokens", 0)),
            },
            stop_reason=data.get("stop_reason"),
            raw=data,
        )

    async def createMessage(
        self,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ProviderResponse:
        payload = dict(params or {})
        payload.update(kwargs)
        raw_messages = list(payload.get("messages") or [])
        normalized_messages: list[ConversationMessage] = []
        for item in raw_messages:
            if isinstance(item, ConversationMessage):
                normalized_messages.append(item)
            elif isinstance(item, dict):
                normalized_messages.append(
                    ConversationMessage(role=str(item.get("role") or "user"), content=item.get("content"))
                )
        raw_tools = list(payload.get("tools") or [])
        normalized_tools: list[ToolDefinition] = [t for t in raw_tools if isinstance(t, ToolDefinition)]
        return await self.create_message(
            model=str(payload.get("model") or ""),
            system_prompt=str(payload.get("system") or payload.get("system_prompt") or ""),
            messages=normalized_messages,
            tools=normalized_tools,
            max_tokens=payload.get("maxTokens") or payload.get("max_tokens"),
            thinking=payload.get("thinking"),
        )

