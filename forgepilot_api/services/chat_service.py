from __future__ import annotations

import json
from typing import AsyncGenerator

import httpx

from forgepilot_api.models import ConversationMessage, ModelConfig
from forgepilot_api.services.codex_config_service import load_codex_runtime_config

DEFAULT_MODEL = "claude-sonnet-4-20250514"
MAX_CONTEXT_MESSAGES = 40


def _is_anthropic_model(model: str) -> bool:
    lowered = model.lower()
    return lowered.startswith("claude-") or "claude" in lowered


def _is_aborted(abort_controller: object | None) -> bool:
    if abort_controller is None:
        return False
    try:
        signal = getattr(abort_controller, "signal", None)
        if signal is not None and bool(getattr(signal, "aborted", False)):
            return True
    except Exception:
        pass
    try:
        if bool(getattr(abort_controller, "aborted", False)):
            return True
    except Exception:
        pass
    try:
        is_set = getattr(abort_controller, "is_set", None)
        if callable(is_set) and bool(is_set()):
            return True
    except Exception:
        pass
    return False


def _resolve_config(model_config: ModelConfig | None) -> tuple[str, str | None, str, str | None]:
    codex_cfg = load_codex_runtime_config()
    api_key = (model_config.apiKey if model_config else None) or str(codex_cfg.get("apiKey") or "")
    base_url = (model_config.baseUrl if model_config else None) or (str(codex_cfg.get("baseUrl") or "") or None)
    model = (model_config.model if model_config else None) or str(codex_cfg.get("model") or "") or DEFAULT_MODEL
    api_type = (model_config.apiType if model_config else None) or (str(codex_cfg.get("apiType") or "") or None)
    return api_key or "", base_url, model, api_type


def _build_system_prompt(base: str, language: str | None) -> str:
    if not language:
        return base
    lang_map = {
        "zh-CN": "Chinese (Simplified)",
        "zh-TW": "Chinese (Traditional)",
        "en-US": "English",
        "ja-JP": "Japanese",
        "ko-KR": "Korean",
    }
    return f"{base} Please respond in {lang_map.get(language, language)}."


def _to_openai_endpoint(base_url: str | None) -> str:
    if base_url:
        base = base_url.rstrip("/")
        return f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
    return "https://api.openai.com/v1/chat/completions"


def _to_anthropic_endpoint(base_url: str | None) -> str:
    base = (base_url or "https://api.anthropic.com").rstrip("/")
    return f"{base}/v1/messages"


def _trim_conversation(
    conversation: list[ConversationMessage] | None,
) -> list[dict[str, str]]:
    if not conversation:
        return []
    sliced = conversation[-MAX_CONTEXT_MESSAGES:]
    return [{"role": msg.role, "content": msg.content} for msg in sliced]


async def _run_openai_compatible_chat(
    *,
    messages: list[dict[str, str]],
    system_prompt: str,
    api_key: str,
    base_url: str | None,
    model: str,
    abort_controller: object | None = None,
) -> AsyncGenerator[dict[str, str], None]:
    endpoint = _to_openai_endpoint(base_url)
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "max_tokens": 4096,
        "stream": True,
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", endpoint, headers=headers, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if _is_aborted(abort_controller):
                    return
                if not line:
                    continue
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    parsed = json.loads(data)
                except Exception:
                    continue
                content = (
                    parsed.get("choices", [{}])[0]
                    .get("delta", {})
                    .get("content")
                )
                if content:
                    yield {"type": "text", "content": str(content)}


async def _run_anthropic_chat(
    *,
    messages: list[dict[str, str]],
    system_prompt: str,
    api_key: str,
    base_url: str | None,
    model: str,
    abort_controller: object | None = None,
) -> AsyncGenerator[dict[str, str], None]:
    endpoint = _to_anthropic_endpoint(base_url)
    payload = {
        "model": model,
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": messages,
        "stream": True,
        "thinking": {"type": "disabled"},
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    event_type: str | None = None
    data_lines: list[str] = []

    async def emit_event() -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        if not data_lines:
            return out
        raw = "\n".join(data_lines).strip()
        if not raw:
            return out
        try:
            parsed = json.loads(raw)
        except Exception:
            return out

        local_event_type = event_type or str(parsed.get("type") or "")
        if local_event_type == "content_block_delta":
            delta = parsed.get("delta", {})
            if isinstance(delta, dict) and delta.get("type") == "text_delta" and delta.get("text"):
                out.append({"type": "text", "content": str(delta["text"])})
        return out

    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", endpoint, headers=headers, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if _is_aborted(abort_controller):
                    return
                if line is None:
                    continue
                text = line.rstrip("\r")
                if not text:
                    events = await emit_event()
                    for event in events:
                        yield event
                    event_type = None
                    data_lines = []
                    continue
                if text.startswith(":"):
                    continue
                if text.startswith("event:"):
                    event_type = text[6:].strip()
                    continue
                if text.startswith("data:"):
                    data_lines.append(text[5:].lstrip())
                    continue

            if data_lines:
                events = await emit_event()
                for event in events:
                    yield event


async def run_chat(
    prompt: str,
    model_config: ModelConfig | None = None,
    language: str | None = None,
    conversation: list[ConversationMessage] | None = None,
    abort_controller: object | None = None,
) -> AsyncGenerator[dict, None]:
    api_key, base_url, model, api_type = _resolve_config(model_config)
    if not api_key:
        yield {"type": "error", "message": "No API key configured. Please set up your API key in Settings."}
        yield {"type": "done"}
        return

    system_prompt = _build_system_prompt(
        "You are a helpful assistant. Be concise and direct in your responses. "
        "You have network access capabilities. When users ask about URLs or websites, "
        "help with analysis and suggest switching to task mode for full tool-based web access.",
        language,
    )
    messages = _trim_conversation(conversation)
    messages.append({"role": "user", "content": prompt})

    use_anthropic = api_type == "anthropic-messages" or (
        api_type is None and _is_anthropic_model(model)
    )
    try:
        if _is_aborted(abort_controller):
            yield {"type": "done"}
            return
        if use_anthropic:
            async for event in _run_anthropic_chat(
                messages=messages,
                system_prompt=system_prompt,
                api_key=api_key,
                base_url=base_url,
                model=model,
                abort_controller=abort_controller,
            ):
                yield event
        else:
            async for event in _run_openai_compatible_chat(
                messages=messages,
                system_prompt=system_prompt,
                api_key=api_key,
                base_url=base_url,
                model=model,
                abort_controller=abort_controller,
            ):
                yield event
    except Exception as exc:
        if _is_aborted(abort_controller):
            yield {"type": "done"}
            return
        yield {"type": "error", "message": str(exc)}
    yield {"type": "done"}


async def _openai_create(
    *,
    messages: list[dict[str, str]],
    system_prompt: str,
    api_key: str,
    base_url: str | None,
    model: str,
    max_tokens: int,
) -> str:
    endpoint = _to_openai_endpoint(base_url)
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(endpoint, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    return str(data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()


async def _anthropic_create(
    *,
    prompt: str,
    system_prompt: str,
    api_key: str,
    base_url: str | None,
    model: str,
    max_tokens: int,
) -> str:
    endpoint = _to_anthropic_endpoint(base_url)
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": prompt}],
        "thinking": {"type": "disabled"},
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(endpoint, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    chunks: list[str] = []
    for block in data.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
            chunks.append(str(block["text"]))
    return "".join(chunks).strip()


async def generate_title(
    prompt: str,
    model_config: ModelConfig | None = None,
    language: str | None = None,
) -> str:
    del language
    api_key, base_url, model, api_type = _resolve_config(model_config)
    if not api_key:
        plain = prompt.strip().replace("\n", " ")
        return plain[:30] + ("..." if len(plain) > 30 else "")

    system_prompt = (
        "Generate a very short title (max 20 characters) that summarizes the user's request. "
        "Output only the title, no quotes, no ending punctuation, no explanation."
    )
    try:
        use_anthropic = api_type == "anthropic-messages" or (
            api_type is None and _is_anthropic_model(model)
        )
        if use_anthropic:
            title = await _anthropic_create(
                prompt=prompt,
                system_prompt=system_prompt,
                api_key=api_key,
                base_url=base_url,
                model=model,
                max_tokens=50,
            )
        else:
            title = await _openai_create(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=system_prompt,
                api_key=api_key,
                base_url=base_url,
                model=model,
                max_tokens=50,
            )
        if title:
            return title[:80]
    except Exception:
        pass

    plain = prompt.strip().replace("\n", " ")
    return plain[:30] + ("..." if len(plain) > 30 else "")

