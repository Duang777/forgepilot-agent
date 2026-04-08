from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from forgepilot_sdk.providers.base import LLMProvider, ProviderResponse, ProviderToolCall
from forgepilot_sdk.types import ConversationMessage, ToolDefinition


def _to_openai_tool(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _to_openai_message(msg: ConversationMessage) -> dict[str, Any]:
    if msg.role == "tool":
        payload = msg.content if isinstance(msg.content, dict) else {}
        return {
            "role": "tool",
            "tool_call_id": payload.get("tool_call_id", ""),
            "content": payload.get("content", ""),
        }
    if msg.role == "assistant" and isinstance(msg.content, dict):
        text = msg.content.get("text")
        message: dict[str, Any] = {
            "role": "assistant",
            "content": text if isinstance(text, str) else "",
        }
        raw_calls = msg.content.get("tool_calls")
        if isinstance(raw_calls, list) and raw_calls:
            tool_calls: list[dict[str, Any]] = []
            for raw in raw_calls:
                if not isinstance(raw, dict):
                    continue
                arguments = raw.get("input")
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments or {}, ensure_ascii=False)
                tool_calls.append(
                    {
                        "id": raw.get("id") or str(uuid.uuid4()),
                        "type": "function",
                        "function": {
                            "name": raw.get("name") or "",
                            "arguments": arguments,
                        },
                    }
                )
            if tool_calls:
                message["tool_calls"] = tool_calls
        return message
    return {
        "role": "assistant" if msg.role == "assistant" else "user",
        "content": msg.content if isinstance(msg.content, str) else json.dumps(msg.content, ensure_ascii=False),
    }


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, *, api_key: str, base_url: str | None = None, timeout: float = 120.0) -> None:
        self.api_type = "openai-completions"
        self._api_key = api_key
        self._base_url = (base_url or "https://api.openai.com").rstrip("/")
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
        endpoint = self._build_chat_completions_endpoint()
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system_prompt}]
            + [_to_openai_message(m) for m in messages],
            "temperature": 0.2,
        }
        if tools:
            payload["tools"] = [_to_openai_tool(t) for t in tools]
            payload["tool_choice"] = "auto"
        if max_tokens:
            payload["max_tokens"] = max_tokens

        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(endpoint, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        message = data["choices"][0]["message"]
        content = message.get("content") or ""
        tool_calls: list[ProviderToolCall] = []

        for call in message.get("tool_calls") or []:
            raw_args = call.get("function", {}).get("arguments", "{}")
            try:
                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except Exception:
                parsed_args = {"raw": raw_args}
            tool_calls.append(
                ProviderToolCall(
                    id=call.get("id") or str(uuid.uuid4()),
                    name=call.get("function", {}).get("name", ""),
                    input=parsed_args,
                )
            )

        # Some OpenAI-compatible gateways (including Codex-style proxies) may return
        # empty non-streaming `message.content` while streaming responses contain tokens.
        # Fallback to stream aggregation when both text and tool calls are empty.
        if not content and not tool_calls:
            stream_content, stream_calls, stream_usage, stream_finish_reason = await self._create_message_streaming(
                endpoint=endpoint,
                headers=headers,
                payload=payload,
            )
            if stream_content or stream_calls:
                content = stream_content
                tool_calls = stream_calls
                usage = stream_usage or (data.get("usage") or {})
                stop_reason = stream_finish_reason or data.get("choices", [{}])[0].get("finish_reason")
                return ProviderResponse(
                    content=content,
                    tool_calls=tool_calls,
                    usage={
                        "input_tokens": int(usage.get("prompt_tokens", usage.get("input_tokens", 0))),
                        "output_tokens": int(usage.get("completion_tokens", usage.get("output_tokens", 0))),
                    },
                    stop_reason=stop_reason,
                    raw=data,
                )

        usage = data.get("usage") or {}
        return ProviderResponse(
            content=content,
            tool_calls=tool_calls,
            usage={
                "input_tokens": int(usage.get("prompt_tokens", 0)),
                "output_tokens": int(usage.get("completion_tokens", 0)),
            },
            stop_reason=data.get("choices", [{}])[0].get("finish_reason"),
            raw=data,
        )

    def _build_chat_completions_endpoint(self) -> str:
        if self._base_url.endswith("/v1"):
            return f"{self._base_url}/chat/completions"
        return f"{self._base_url}/v1/chat/completions"

    async def _create_message_streaming(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> tuple[str, list[ProviderToolCall], dict[str, Any], str | None]:
        stream_payload = dict(payload)
        stream_payload["stream"] = True
        stream_payload["stream_options"] = {"include_usage": True}

        content_parts: list[str] = []
        tool_calls_by_index: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        finish_reason: str | None = None

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream("POST", endpoint, headers=headers, json=stream_payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        line = line[6:]
                    if line == "[DONE]":
                        break
                    try:
                        chunk = json.loads(line)
                    except Exception:
                        continue

                    if isinstance(chunk.get("usage"), dict):
                        usage = chunk["usage"]

                    choice = (chunk.get("choices") or [{}])[0]
                    if not isinstance(choice, dict):
                        continue

                    finish_reason = choice.get("finish_reason") or finish_reason
                    delta = choice.get("delta") or {}
                    if not isinstance(delta, dict):
                        continue

                    text_delta = delta.get("content")
                    if isinstance(text_delta, str):
                        content_parts.append(text_delta)

                    for call in delta.get("tool_calls") or []:
                        if not isinstance(call, dict):
                            continue
                        idx = int(call.get("index", 0))
                        state = tool_calls_by_index.setdefault(
                            idx,
                            {
                                "id": call.get("id") or str(uuid.uuid4()),
                                "name": "",
                                "arguments": "",
                            },
                        )
                        if call.get("id"):
                            state["id"] = call["id"]
                        fn = call.get("function") or {}
                        if isinstance(fn, dict):
                            if fn.get("name"):
                                state["name"] = fn["name"]
                            if fn.get("arguments"):
                                state["arguments"] += str(fn["arguments"])

        tool_calls: list[ProviderToolCall] = []
        for idx in sorted(tool_calls_by_index.keys()):
            call = tool_calls_by_index[idx]
            raw_args = call.get("arguments") or "{}"
            try:
                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except Exception:
                parsed_args = {"raw": raw_args}
            tool_calls.append(
                ProviderToolCall(
                    id=str(call.get("id") or str(uuid.uuid4())),
                    name=str(call.get("name") or ""),
                    input=parsed_args,
                )
            )

        return "".join(content_parts), tool_calls, usage, finish_reason

