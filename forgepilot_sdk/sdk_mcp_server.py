from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from forgepilot_sdk.tool_helper import SdkMcpToolDefinition, sdk_tool_to_tool_definition
from forgepilot_sdk.types import ToolDefinition


@dataclass(slots=True)
class McpSdkServerConfig:
    type: str
    name: str
    version: str
    tools: list[ToolDefinition] = field(default_factory=list)
    _sdk_tools: list[SdkMcpToolDefinition] = field(default_factory=list)


def create_sdk_mcp_server(options: dict[str, Any]) -> McpSdkServerConfig:
    name = str(options.get("name") or "").strip()
    if not name:
        raise ValueError("create_sdk_mcp_server requires a non-empty 'name'.")
    version = str(options.get("version") or "1.0.0")
    sdk_tools_raw = options.get("tools")
    sdk_tools = [tool for tool in (sdk_tools_raw if isinstance(sdk_tools_raw, list) else []) if isinstance(tool, SdkMcpToolDefinition)]

    converted_tools: list[ToolDefinition] = []
    for sdk_tool in sdk_tools:
        tool_def = sdk_tool_to_tool_definition(sdk_tool)
        converted_tools.append(
            ToolDefinition(
                name=f"mcp__{name}__{sdk_tool.name}",
                description=tool_def.description,
                input_schema=tool_def.input_schema,
                call=tool_def.call,
                read_only=tool_def.read_only,
                concurrency_safe=tool_def.concurrency_safe,
                enabled=tool_def.enabled,
                prompt_fn=tool_def.prompt_fn,
                is_read_only_fn=tool_def.is_read_only_fn,
                is_concurrency_safe_fn=tool_def.is_concurrency_safe_fn,
                is_enabled_fn=tool_def.is_enabled_fn,
            )
        )

    return McpSdkServerConfig(
        type="sdk",
        name=name,
        version=version,
        tools=converted_tools,
        _sdk_tools=sdk_tools,
    )


def is_sdk_server_config(config: Any) -> bool:
    return bool(
        isinstance(config, (dict, McpSdkServerConfig))
        and str(getattr(config, "type", None) if isinstance(config, McpSdkServerConfig) else config.get("type")) == "sdk"
        and isinstance(getattr(config, "tools", None) if isinstance(config, McpSdkServerConfig) else config.get("tools"), list)
    )


def createSdkMcpServer(options: dict[str, Any]) -> McpSdkServerConfig:
    return create_sdk_mcp_server(options)


def isSdkServerConfig(config: Any) -> bool:
    return is_sdk_server_config(config)
