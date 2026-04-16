from __future__ import annotations

import asyncio

from forgepilot_api.models import ModelConfig
from forgepilot_api.services import chat_service


def _collect(async_gen):
    async def _run():
        out = []
        async for item in async_gen:
            out.append(item)
        return out

    return asyncio.run(_run())


def test_chat_endpoint_helpers() -> None:
    assert chat_service._to_openai_endpoint("https://api.example.com/v1") == "https://api.example.com/v1/chat/completions"
    assert chat_service._to_openai_endpoint("https://api.example.com") == "https://api.example.com/v1/chat/completions"
    assert chat_service._to_anthropic_endpoint("https://api.anthropic.com") == "https://api.anthropic.com/v1/messages"


def test_run_chat_without_api_key_returns_error(monkeypatch) -> None:
    monkeypatch.setattr(chat_service, "load_codex_runtime_config", lambda: {})
    events = _collect(chat_service.run_chat("hello", model_config=None))
    assert events[0]["type"] == "error"
    assert "No API key configured" in events[0]["message"]
    assert events[-1]["type"] == "done"


def test_generate_title_fallback_without_api_key(monkeypatch) -> None:
    monkeypatch.setattr(chat_service, "load_codex_runtime_config", lambda: {})
    title = asyncio.run(chat_service.generate_title("This is a long prompt for title generation", None))
    assert isinstance(title, str)
    assert len(title) > 0


def test_resolve_config_with_model_config() -> None:
    cfg = ModelConfig(
        apiKey="key",
        baseUrl="https://api.example.com",
        model="gpt-4o",
        apiType="openai-completions",
    )
    api_key, base_url, model, api_type = chat_service._resolve_config(cfg)
    assert api_key == "key"
    assert base_url == "https://api.example.com"
    assert model == "gpt-4o"
    assert api_type == "openai-completions"


def test_resolve_config_uses_codex_runtime_when_model_config_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        chat_service,
        "load_codex_runtime_config",
        lambda: {
            "apiKey": "codex-key",
            "baseUrl": "https://codex.example.com",
            "model": "gpt-5.4",
            "apiType": "openai-completions",
        },
    )
    api_key, base_url, model, api_type = chat_service._resolve_config(None)
    assert api_key == "codex-key"
    assert base_url == "https://codex.example.com"
    assert model == "gpt-5.4"
    assert api_type == "openai-completions"


def test_run_chat_returns_done_when_aborted_before_start(monkeypatch) -> None:
    monkeypatch.setattr(
        chat_service,
        "load_codex_runtime_config",
        lambda: {
            "apiKey": "k",
            "baseUrl": "https://api.example.com",
            "model": "gpt-4o",
            "apiType": "openai-completions",
        },
    )
    abort_event = asyncio.Event()
    abort_event.set()
    events = _collect(chat_service.run_chat("hello", model_config=None, abort_controller=abort_event))
    assert events == [{"type": "done"}]

