from __future__ import annotations

import asyncio
from pathlib import Path

from forgepilot_sdk.engine import QueryEngine
from forgepilot_sdk.providers.base import ProviderResponse, ProviderToolCall
from forgepilot_sdk.tools.base import define_tool
from forgepilot_sdk.types import ToolContext, ToolResult


class _FakeProvider:
    api_type = "openai-completions"

    def __init__(self) -> None:
        self._turn = 0

    async def create_message(self, **kwargs) -> ProviderResponse:  # type: ignore[override]
        del kwargs
        self._turn += 1
        if self._turn == 1:
            return ProviderResponse(
                content="",
                tool_calls=[ProviderToolCall(id="tool-1", name="Bash", input={"command": "echo hi"})],
                usage={"input_tokens": 1, "output_tokens": 1},
            )
        return ProviderResponse(content="done", tool_calls=[], usage={"input_tokens": 1, "output_tokens": 1})


def _collect(async_gen):
    async def _run():
        items = []
        async for item in async_gen:
            items.append(item)
        return items

    return asyncio.run(_run())


def test_engine_emits_permission_request_and_denies_tool() -> None:
    provider = _FakeProvider()
    executed = {"called": False}
    requested: list[dict] = []

    async def _tool_call(input_data: dict, ctx: ToolContext) -> ToolResult:
        del input_data, ctx
        executed["called"] = True
        return ToolResult(content="ok", is_error=False)

    async def _on_permission(permission: dict) -> None:
        requested.append(permission)

    async def _wait(_permission_id: str) -> bool:
        return False

    tool = define_tool(
        name="Bash",
        description="Run bash",
        input_schema={"type": "object", "properties": {"command": {"type": "string"}}},
        call=_tool_call,
        read_only=False,
        concurrency_safe=False,
    )

    engine = QueryEngine(
        provider=provider,  # type: ignore[arg-type]
        model="gpt-4o",
        tools=[tool],
        cwd=Path.cwd(),
        max_turns=3,
        permission_mode="default",
        on_permission_request=_on_permission,
        wait_for_permission_decision=_wait,
    )

    events = _collect(engine.submit_message("run command"))
    subtypes = [e.get("subtype") for e in events if e.get("type") == "system"]
    assert "permission_request" in subtypes
    assert requested and requested[0]["toolName"] == "Bash"
    assert executed["called"] is False
    tool_results = [e for e in events if e.get("type") == "tool_result"]
    assert tool_results
    assert tool_results[0]["result"]["is_error"] is True
    assert "Permission denied" in tool_results[0]["result"]["output"]


def test_engine_permission_approved_executes_tool() -> None:
    provider = _FakeProvider()
    executed = {"called": False}

    async def _tool_call(input_data: dict, ctx: ToolContext) -> ToolResult:
        del input_data, ctx
        executed["called"] = True
        return ToolResult(content="ok", is_error=False)

    async def _wait(_permission_id: str) -> bool:
        return True

    tool = define_tool(
        name="Bash",
        description="Run bash",
        input_schema={"type": "object", "properties": {"command": {"type": "string"}}},
        call=_tool_call,
        read_only=False,
        concurrency_safe=False,
    )

    engine = QueryEngine(
        provider=provider,  # type: ignore[arg-type]
        model="gpt-4o",
        tools=[tool],
        cwd=Path.cwd(),
        max_turns=3,
        permission_mode="default",
        wait_for_permission_decision=_wait,
    )

    _collect(engine.submit_message("run command"))
    assert executed["called"] is True


def test_engine_init_event_reports_configured_permission_mode() -> None:
    provider = _FakeProvider()

    async def _tool_call(input_data: dict, ctx: ToolContext) -> ToolResult:
        del input_data, ctx
        return ToolResult(content="ok", is_error=False)

    tool = define_tool(
        name="Bash",
        description="Run bash",
        input_schema={"type": "object", "properties": {"command": {"type": "string"}}},
        call=_tool_call,
        read_only=False,
        concurrency_safe=False,
    )

    engine = QueryEngine(
        provider=provider,  # type: ignore[arg-type]
        model="gpt-4o",
        tools=[tool],
        cwd=Path.cwd(),
        max_turns=1,
        permission_mode="default",
    )

    events = _collect(engine.submit_message("hello"))
    init_events = [event for event in events if event.get("type") == "system" and event.get("subtype") == "init"]
    assert init_events
    assert init_events[0]["permission_mode"] == "default"

