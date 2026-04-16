from __future__ import annotations

import asyncio
from pathlib import Path

from forgepilot_sdk.hooks import HookDefinition, create_hook_registry
from forgepilot_sdk.tool_helper import sdk_tool_to_tool_definition, tool
from forgepilot_sdk.types import ConversationMessage, ToolContext
from forgepilot_sdk.utils.compact import create_auto_compact_state, micro_compact_messages, should_auto_compact
from forgepilot_sdk.utils.messages import create_assistant_message, create_user_message, normalize_messages_for_api
from forgepilot_sdk.utils.retry import RetryConfig, with_retry


def _run(coro):
    return asyncio.run(coro)


def test_hook_registry_executes_handler_and_matcher() -> None:
    async def _handler(payload):
        return {"message": f"ok:{payload.get('toolName')}"}

    registry = create_hook_registry(
        {
            "PreToolUse": [
                HookDefinition(handler=_handler, matcher="^Read$", timeout=5000),
            ]
        }
    )
    outputs = _run(
        registry.execute(
            "PreToolUse",
            {"event": "PreToolUse", "toolName": "Read", "sessionId": "s1", "cwd": str(Path.cwd())},
        )
    )
    assert outputs and outputs[0]["message"] == "ok:Read"


def test_tool_helper_converts_sdk_tool_definition() -> None:
    async def _handler(args, _extra):
        return {"content": [{"type": "text", "text": f"hello {args.get('name', '')}"}], "isError": False}

    sdk = tool(
        "hello_tool",
        "Say hello",
        {"type": "object", "properties": {"name": {"type": "string"}}},
        _handler,
        extras={"annotations": {"readOnlyHint": True}},
    )
    converted = sdk_tool_to_tool_definition(sdk)
    assert converted.is_read_only() is True
    result = _run(converted.call({"name": "forgepilot"}, ToolContext(cwd=Path.cwd())))
    assert result.is_error is False
    assert "hello forgepilot" in str(result.content)


def test_message_normalize_retry_and_micro_compact() -> None:
    user_message = create_user_message("hello")
    assistant_message = create_assistant_message([{"type": "text", "text": "world"}], usage={"input_tokens": 1})
    assert user_message["type"] == "user"
    assert assistant_message["type"] == "assistant"
    assert assistant_message["usage"]["input_tokens"] == 1

    messages = [
        ConversationMessage(role="user", content="hello"),
        ConversationMessage(role="user", content="world"),
        ConversationMessage(
            role="assistant",
            content=[{"type": "tool_use", "id": "u-1", "name": "Read", "input": {"path": "a.txt"}}],
        ),
        ConversationMessage(
            role="user",
            content=[
                {"type": "tool_result", "tool_use_id": "u-1", "content": "ok"},
                {"type": "tool_result", "tool_use_id": "u-2", "content": "orphan"},
            ],
        ),
    ]
    normalized = normalize_messages_for_api(messages)
    assert isinstance(normalized[0].content, list)
    assert len(normalized[0].content) == 2
    assert len(normalized[-1].content) == 1

    compacted = micro_compact_messages(
        [
            ConversationMessage(
                role="user",
                content=[{"type": "tool_result", "tool_use_id": "u", "content": "x" * 1200}],
            )
        ],
        max_tool_result_chars=100,
    )
    assert "...(truncated)..." in compacted[0].content[0]["content"]  # type: ignore[index]

    state = create_auto_compact_state()
    assert should_auto_compact(normalized, "gpt-4o", state) is False

    attempts = {"n": 0}

    async def _flaky():
        attempts["n"] += 1
        if attempts["n"] < 2:
            err = RuntimeError("retryable")
            setattr(err, "status", 429)
            raise err
        return "ok"

    value = _run(with_retry(_flaky, RetryConfig(max_retries=2, base_delay_ms=1, max_delay_ms=2)))
    assert value == "ok"
