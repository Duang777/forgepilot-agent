from __future__ import annotations

from forgepilot_sdk.providers.openai_compatible import OpenAICompatibleProvider


def test_openai_compatible_endpoint_supports_v4_base_url() -> None:
    provider = OpenAICompatibleProvider(api_key="test", base_url="https://open.bigmodel.cn/api/paas/v4")

    assert provider._build_chat_completions_endpoint() == "https://open.bigmodel.cn/api/paas/v4/chat/completions"


def test_openai_compatible_endpoint_accepts_full_chat_completions_url() -> None:
    provider = OpenAICompatibleProvider(api_key="test", base_url="https://api.example.com/v1/chat/completions")

    assert provider._build_chat_completions_endpoint() == "https://api.example.com/v1/chat/completions"
