from __future__ import annotations

from forgepilot_sdk.types import ToolCallable, ToolDefinition


def define_tool(
    *,
    name: str,
    description: str,
    input_schema: dict,
    call: ToolCallable,
    read_only: bool = False,
    concurrency_safe: bool = True,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        input_schema=input_schema,
        call=call,
        read_only=read_only,
        concurrency_safe=concurrency_safe,
    )


