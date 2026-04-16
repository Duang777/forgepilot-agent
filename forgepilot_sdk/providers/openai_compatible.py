from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any

import httpx

from forgepilot_sdk.providers.base import LLMProvider, ProviderResponse, ProviderToolCall
from forgepilot_sdk.types import ConversationMessage, ToolDefinition

logger = logging.getLogger(__name__)
_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def _to_openai_tool(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _tool_result_content_to_string(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        text_parts: list[str] = []
        for item in raw:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
        if text_parts:
            return "\n".join(text_parts)
    return json.dumps(raw, ensure_ascii=False)


def _assistant_legacy_dict_to_blocks(content: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    text = content.get("text")
    if isinstance(text, str):
        blocks.append({"type": "text", "text": text})
    raw_calls = content.get("tool_calls")
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
    return blocks


def _to_openai_messages(messages: list[ConversationMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == "tool":
            payload = msg.content if isinstance(msg.content, dict) else {}
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": payload.get("tool_call_id", ""),
                    "content": _tool_result_content_to_string(payload.get("content", "")),
                }
            )
            continue

        if msg.role == "assistant":
            content = msg.content
            if isinstance(content, str):
                out.append({"role": "assistant", "content": content})
                continue
            if isinstance(content, dict):
                content = _assistant_legacy_dict_to_blocks(content)

            if isinstance(content, list):
                text_parts: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and isinstance(block.get("text"), str):
                        text_parts.append(str(block["text"]))
                    if block.get("type") == "tool_use":
                        arguments = block.get("input")
                        if not isinstance(arguments, str):
                            arguments = json.dumps(arguments or {}, ensure_ascii=False)
                        tool_calls.append(
                            {
                                "id": block.get("id") or str(uuid.uuid4()),
                                "type": "function",
                                "function": {
                                    "name": block.get("name") or "",
                                    "arguments": arguments,
                                },
                            }
                        )
                entry: dict[str, Any] = {
                    "role": "assistant",
                    "content": "\n".join(text_parts) if text_parts else None,
                }
                if tool_calls:
                    entry["tool_calls"] = tool_calls
                out.append(entry)
                continue

            out.append({"role": "assistant", "content": json.dumps(msg.content, ensure_ascii=False)})
            continue

        # user role
        if isinstance(msg.content, str):
            out.append({"role": "user", "content": msg.content})
            continue

        if isinstance(msg.content, list):
            text_parts: list[str] = []
            tool_results: list[dict[str, Any]] = []
            for block in msg.content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "text" and isinstance(block.get("text"), str):
                    text_parts.append(str(block["text"]))
                elif block_type == "tool_result":
                    tool_results.append(
                        {
                            "tool_use_id": block.get("tool_use_id") or "",
                            "content": _tool_result_content_to_string(block.get("content", "")),
                        }
                    )
            for tr in tool_results:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": tr["tool_use_id"],
                        "content": tr["content"],
                    }
                )
            if text_parts:
                out.append({"role": "user", "content": "\n".join(text_parts)})
            continue

        out.append({"role": "user", "content": json.dumps(msg.content, ensure_ascii=False)})
    return out


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, *, api_key: str, base_url: str | None = None, timeout: float = 120.0) -> None:
        self.api_type = "openai-completions"
        self._api_key = api_key
        self._base_url = (base_url or "https://api.openai.com").rstrip("/")
        self._timeout = timeout
        self._max_retries = max(0, int(os.getenv("FORGEPILOT_LLM_MAX_RETRIES", "2")))
        self._retry_backoff_seconds = max(
            0.0,
            float(os.getenv("FORGEPILOT_LLM_RETRY_BACKOFF_SECONDS", "0.8")),
        )
        self._retry_max_backoff_seconds = max(
            self._retry_backoff_seconds,
            float(os.getenv("FORGEPILOT_LLM_RETRY_MAX_BACKOFF_SECONDS", "6.0")),
        )

    def _retry_delay(self, attempt: int) -> float:
        delay = self._retry_backoff_seconds * (2**attempt)
        return min(delay, self._retry_max_backoff_seconds)

    @staticmethod
    def _extract_error_detail(response: httpx.Response, fallback_text: str = "") -> str:
        try:
            data = response.json()
            if isinstance(data, dict):
                error_obj = data.get("error")
                if isinstance(error_obj, dict):
                    msg = error_obj.get("message")
                    if isinstance(msg, str) and msg.strip():
                        return msg.strip()
                msg = data.get("message")
                if isinstance(msg, str) and msg.strip():
                    return msg.strip()
        except Exception:
            pass

        text = fallback_text
        if not text:
            try:
                text = response.text
            except Exception:
                text = ""
        text = str(text or "").replace("\r", " ").replace("\n", " ").strip()
        if len(text) > 260:
            text = f"{text[:260]}..."
        return text or "No error detail from upstream."

    def _build_http_status_error(
        self,
        *,
        response: httpx.Response,
        detail_override: str = "",
    ) -> RuntimeError:
        reason = response.reason_phrase or "Unknown Error"
        detail = self._extract_error_detail(response, detail_override)
        return RuntimeError(
            f"Server error '{response.status_code} {reason}' for url '{response.request.url}' | Detail: {detail}"
        )

    @staticmethod
    def _is_retryable_transport_error(exc: Exception) -> bool:
        return isinstance(
            exc,
            (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.NetworkError,
                httpx.ReadError,
                httpx.WriteError,
                httpx.RemoteProtocolError,
                httpx.PoolTimeout,
            ),
        )

    async def _post_json_with_retry(
        self,
        *,
        client: httpx.AsyncClient,
        endpoint: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            try:
                resp = await client.post(endpoint, headers=headers, json=payload)
                if resp.status_code >= 400:
                    if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                        delay = self._retry_delay(attempt)
                        logger.warning(
                            "openai-compatible request failed status=%s, retrying in %.2fs (attempt %s/%s) endpoint=%s",
                            resp.status_code,
                            delay,
                            attempt + 1,
                            self._max_retries + 1,
                            endpoint,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise self._build_http_status_error(response=resp)
                return resp.json()
            except Exception as exc:
                if self._is_retryable_transport_error(exc) and attempt < self._max_retries:
                    delay = self._retry_delay(attempt)
                    logger.warning(
                        "openai-compatible request transport error=%s, retrying in %.2fs (attempt %s/%s) endpoint=%s",
                        type(exc).__name__,
                        delay,
                        attempt + 1,
                        self._max_retries + 1,
                        endpoint,
                    )
                    await asyncio.sleep(delay)
                    continue
                if self._is_retryable_transport_error(exc):
                    raise RuntimeError(f"Upstream API request failed: {type(exc).__name__}: {exc}") from exc
                if isinstance(exc, RuntimeError):
                    raise
                raise
        raise RuntimeError("Upstream API request failed after retries.")

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
        del thinking
        endpoint = self._build_chat_completions_endpoint()
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system_prompt}] + _to_openai_messages(messages),
            "temperature": 0.2,
        }
        if tools:
            payload["tools"] = [_to_openai_tool(t) for t in tools]
            payload["tool_choice"] = "auto"
        if max_tokens:
            payload["max_tokens"] = max_tokens

        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            data = await self._post_json_with_retry(
                client=client,
                endpoint=endpoint,
                headers=headers,
                payload=payload,
            )

        message = data["choices"][0]["message"]
        content = message.get("content") or ""
        tool_calls: list[ProviderToolCall] = []
        content_blocks: list[dict[str, Any]] = []
        if isinstance(content, str) and content:
            content_blocks.append({"type": "text", "text": content})

        for call in message.get("tool_calls") or []:
            raw_args = call.get("function", {}).get("arguments", "{}")
            try:
                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except Exception:
                parsed_args = {"raw": raw_args}
            tool_id = call.get("id") or str(uuid.uuid4())
            tool_name = call.get("function", {}).get("name", "")
            tool_calls.append(ProviderToolCall(id=tool_id, name=tool_name, input=parsed_args))
            content_blocks.append({"type": "tool_use", "id": tool_id, "name": tool_name, "input": parsed_args})

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
                content_blocks = []
                if stream_content:
                    content_blocks.append({"type": "text", "text": stream_content})
                for call in stream_calls:
                    content_blocks.append(
                        {"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}
                    )
                usage = stream_usage or (data.get("usage") or {})
                stop_reason = stream_finish_reason or data.get("choices", [{}])[0].get("finish_reason")
                return ProviderResponse(
                    content=content_blocks if content_blocks else content,
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
            content=content_blocks if content_blocks else content,
            tool_calls=tool_calls,
            usage={
                "input_tokens": int(usage.get("prompt_tokens", usage.get("input_tokens", 0))),
                "output_tokens": int(usage.get("completion_tokens", usage.get("output_tokens", 0))),
            },
            stop_reason=data.get("choices", [{}])[0].get("finish_reason"),
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
        for attempt in range(self._max_retries + 1):
            content_parts: list[str] = []
            tool_calls_by_index: dict[int, dict[str, Any]] = {}
            usage: dict[str, Any] = {}
            finish_reason: str | None = None

            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    async with client.stream("POST", endpoint, headers=headers, json=stream_payload) as resp:
                        if resp.status_code >= 400:
                            body = (await resp.aread()).decode("utf-8", errors="replace")
                            if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                                delay = self._retry_delay(attempt)
                                logger.warning(
                                    "openai-compatible stream failed status=%s, retrying in %.2fs (attempt %s/%s) endpoint=%s",
                                    resp.status_code,
                                    delay,
                                    attempt + 1,
                                    self._max_retries + 1,
                                    endpoint,
                                )
                                await asyncio.sleep(delay)
                                continue
                            raise self._build_http_status_error(response=resp, detail_override=body)

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
            except Exception as exc:
                if self._is_retryable_transport_error(exc) and attempt < self._max_retries:
                    delay = self._retry_delay(attempt)
                    logger.warning(
                        "openai-compatible stream transport error=%s, retrying in %.2fs (attempt %s/%s) endpoint=%s",
                        type(exc).__name__,
                        delay,
                        attempt + 1,
                        self._max_retries + 1,
                        endpoint,
                    )
                    await asyncio.sleep(delay)
                    continue
                if self._is_retryable_transport_error(exc):
                    raise RuntimeError(f"Upstream API stream request failed: {type(exc).__name__}: {exc}") from exc
                if isinstance(exc, RuntimeError):
                    raise
                raise

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

        raise RuntimeError("Upstream API streaming request failed after retries.")

