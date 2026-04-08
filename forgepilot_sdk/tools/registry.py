from __future__ import annotations

from forgepilot_sdk.tools.core import build_core_tools
from forgepilot_sdk.types import ToolDefinition


def get_all_base_tools() -> list[ToolDefinition]:
    return build_core_tools()


def filter_tools(
    tools: list[ToolDefinition],
    allowed_tools: list[str] | None = None,
    disallowed_tools: list[str] | None = None,
) -> list[ToolDefinition]:
    result = tools
    # `allowed_tools=[]` is an explicit "disable all tools" contract.
    if allowed_tools is not None:
        allow = set(allowed_tools)
        result = [t for t in result if t.name in allow]
    if disallowed_tools:
        deny = set(disallowed_tools)
        result = [t for t in result if t.name not in deny]
    return result


def assemble_tool_pool(
    base_tools: list[ToolDefinition],
    extra_tools: list[ToolDefinition] | None = None,
    allowed_tools: list[str] | None = None,
    disallowed_tools: list[str] | None = None,
) -> list[ToolDefinition]:
    by_name: dict[str, ToolDefinition] = {}
    for tool in base_tools + (extra_tools or []):
        by_name[tool.name] = tool
    return filter_tools(list(by_name.values()), allowed_tools, disallowed_tools)

