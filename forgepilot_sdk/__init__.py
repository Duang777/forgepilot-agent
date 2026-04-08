"""
forgepilot_sdk

Python rewrite of @codeany/open-agent-sdk focused on core 80% compatibility.
"""

from forgepilot_sdk.agent import Agent, create_agent, query
from forgepilot_sdk.providers import (
    AnthropicMessagesProvider,
    OpenAICompatibleProvider,
    create_provider,
)
from forgepilot_sdk.session import (
    append_to_session,
    delete_session,
    fork_session,
    get_session_info,
    get_session_messages,
    list_sessions,
    load_session,
    rename_session,
    save_session,
    tag_session,
)
from forgepilot_sdk.tools import assemble_tool_pool, define_tool, filter_tools, get_all_base_tools
from forgepilot_sdk.types import AgentOptions, QueryRequest, QueryResult, ToolDefinition

__all__ = [
    "Agent",
    "create_agent",
    "query",
    "AgentOptions",
    "QueryRequest",
    "QueryResult",
    "ToolDefinition",
    "define_tool",
    "get_all_base_tools",
    "filter_tools",
    "assemble_tool_pool",
    "create_provider",
    "AnthropicMessagesProvider",
    "OpenAICompatibleProvider",
    "save_session",
    "load_session",
    "list_sessions",
    "fork_session",
    "append_to_session",
    "delete_session",
    "get_session_messages",
    "get_session_info",
    "rename_session",
    "tag_session",
]

