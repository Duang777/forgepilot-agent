from __future__ import annotations

import asyncio
from pathlib import Path

from forgepilot_sdk.tools.base import defineTool
from forgepilot_sdk.types import ToolContext


def _run(coro):
    return asyncio.run(coro)


def test_define_tool_accepts_upstream_config_shape_and_string_result() -> None:
    async def _handler(input_data, _context):
        return f"ok:{input_data.get('name')}"

    tool = defineTool(
        {
            "name": "hello",
            "description": "hello tool",
            "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}},
            "call": _handler,
            "isReadOnly": True,
            "isConcurrencySafe": True,
        }
    )
    assert tool.name == "hello"
    assert tool.is_read_only() is True
    assert tool.is_concurrency_safe() is True
    result = _run(tool.call({"name": "forgepilot"}, ToolContext(cwd=Path.cwd())))
    assert result.is_error is False
    assert str(result.content) == "ok:forgepilot"


def test_define_tool_accepts_object_result_shape() -> None:
    async def _handler(_input_data, _context):
        return {"data": "payload", "is_error": True}

    tool = defineTool(
        {
            "name": "obj",
            "description": "object result",
            "inputSchema": {"type": "object", "properties": {}},
            "call": _handler,
        }
    )
    result = _run(tool.call({}, ToolContext(cwd=Path.cwd())))
    assert result.is_error is True
    assert result.content == "payload"

