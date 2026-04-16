from __future__ import annotations

from typing import Any, Awaitable, Callable

from forgepilot_sdk.types import ToolCallable, ToolContext, ToolDefinition, ToolResult


def define_tool(
    *,
    name: str,
    description: str,
    input_schema: dict,
    call: ToolCallable,
    read_only: bool = False,
    concurrency_safe: bool = False,
    prompt: str | Callable[[ToolContext], Awaitable[str] | str] | None = None,
) -> ToolDefinition:
    prompt_fn = None
    if callable(prompt):
        prompt_fn = prompt
    elif isinstance(prompt, str):
        async def _static_prompt(_context: ToolContext, text: str = prompt) -> str:
            return text

        prompt_fn = _static_prompt
    return ToolDefinition(
        name=name,
        description=description,
        input_schema=input_schema,
        call=call,
        read_only=read_only,
        concurrency_safe=concurrency_safe,
        prompt_fn=prompt_fn,
        is_read_only_fn=lambda: bool(read_only),
        is_concurrency_safe_fn=lambda: bool(concurrency_safe),
        is_enabled_fn=lambda: True,
    )


def to_api_tool(tool: ToolDefinition) -> dict:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }


def defineTool(config: dict[str, Any] | None = None, **kwargs: Any) -> ToolDefinition:
    payload: dict[str, Any] = {}
    if config:
        payload.update(config)
    payload.update(kwargs)

    raw_call = payload.get("call")
    if not callable(raw_call):
        raise ValueError("defineTool requires a callable 'call' handler.")

    async def _call_adapter(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
        try:
            value = raw_call(input_data, context)
            if hasattr(value, "__await__"):
                value = await value
            if isinstance(value, ToolResult):
                return value
            if isinstance(value, str):
                return ToolResult(content=value, is_error=False)
            if isinstance(value, dict):
                output = value.get("data") if "data" in value else value.get("content")
                return ToolResult(content=output if output is not None else "", is_error=bool(value.get("is_error")))
            return ToolResult(content=str(value), is_error=False)
        except Exception as exc:
            return ToolResult(content=f"Error: {exc}", is_error=True)

    return define_tool(
        name=str(payload.get("name") or ""),
        description=str(payload.get("description") or ""),
        input_schema=payload.get("inputSchema") or payload.get("input_schema") or {"type": "object", "properties": {}},
        call=_call_adapter,  # type: ignore[arg-type]
        read_only=bool(payload.get("isReadOnly", payload.get("read_only", False))),
        concurrency_safe=bool(payload.get("isConcurrencySafe", payload.get("concurrency_safe", False))),
        prompt=payload.get("prompt"),
    )


def toApiTool(tool: ToolDefinition) -> dict:
    return to_api_tool(tool)


