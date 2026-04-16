from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from forgepilot_sdk.types import ToolContext, ToolDefinition, ToolResult


@dataclass(slots=True)
class ToolAnnotations:
    readOnlyHint: bool | None = None
    destructiveHint: bool | None = None
    idempotentHint: bool | None = None
    openWorldHint: bool | None = None


CallToolResult = dict[str, Any]
SdkToolHandler = Callable[[dict[str, Any], Any], Awaitable[CallToolResult]]


@dataclass(slots=True)
class SdkMcpToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: SdkToolHandler
    annotations: ToolAnnotations | None = None


def tool(
    name: str,
    description: str,
    input_schema: dict[str, Any],
    handler: SdkToolHandler,
    extras: dict[str, Any] | None = None,
) -> SdkMcpToolDefinition:
    annotations_data = (extras or {}).get("annotations")
    annotations: ToolAnnotations | None = None
    if isinstance(annotations_data, ToolAnnotations):
        annotations = annotations_data
    elif isinstance(annotations_data, dict):
        annotations = ToolAnnotations(
            readOnlyHint=annotations_data.get("readOnlyHint"),
            destructiveHint=annotations_data.get("destructiveHint"),
            idempotentHint=annotations_data.get("idempotentHint"),
            openWorldHint=annotations_data.get("openWorldHint"),
        )
    return SdkMcpToolDefinition(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        annotations=annotations,
    )


def _content_blocks_to_text(content: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            lines.append(str(block))
            continue
        block_type = block.get("type")
        if block_type == "text":
            lines.append(str(block.get("text") or ""))
        elif block_type == "image":
            lines.append(f"[Image: {block.get('mimeType') or 'unknown'}]")
        elif block_type == "resource":
            resource = block.get("resource") or {}
            if isinstance(resource, dict):
                lines.append(str(resource.get("text") or f"[Resource: {resource.get('uri')}]"))
            else:
                lines.append("[Resource]")
        else:
            lines.append(json.dumps(block, ensure_ascii=False))
    return "\n".join([line for line in lines if line])


def sdk_tool_to_tool_definition(sdk_tool: SdkMcpToolDefinition) -> ToolDefinition:
    async def _call(input_data: dict[str, Any], _context: ToolContext) -> ToolResult:
        try:
            result = await sdk_tool.handler(input_data, {})
            content = result.get("content") if isinstance(result, dict) else None
            if isinstance(content, list):
                output = _content_blocks_to_text([x for x in content if isinstance(x, dict)])
            else:
                output = str(content or "")
            return ToolResult(content=output, is_error=bool(result.get("isError")) if isinstance(result, dict) else False)
        except Exception as exc:
            return ToolResult(content=f"Error: {exc}", is_error=True)

    read_only = bool(sdk_tool.annotations.readOnlyHint) if sdk_tool.annotations else False
    return ToolDefinition(
        name=sdk_tool.name,
        description=sdk_tool.description,
        input_schema=sdk_tool.input_schema,
        call=_call,
        read_only=read_only,
        concurrency_safe=read_only,
        is_read_only_fn=lambda: read_only,
        is_concurrency_safe_fn=lambda: read_only,
        is_enabled_fn=lambda: True,
    )


def sdkToolToToolDefinition(sdk_tool: SdkMcpToolDefinition) -> ToolDefinition:
    return sdk_tool_to_tool_definition(sdk_tool)
