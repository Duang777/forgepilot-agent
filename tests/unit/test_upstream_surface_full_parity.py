from __future__ import annotations

import asyncio
import uuid

import forgepilot_sdk as sdk
from forgepilot_sdk.agent import Agent
from forgepilot_sdk.sdk_mcp_server import create_sdk_mcp_server, is_sdk_server_config
from forgepilot_sdk.session import deleteSession, loadSession, saveSession
from forgepilot_sdk.tool_helper import tool
from forgepilot_sdk.types import AgentOptions


def test_sdk_mcp_server_config_works_with_agent_setup() -> None:
    async def _handler(args, _extra):
        return {"content": [{"type": "text", "text": f"hello {args.get('name', '')}"}]}

    sdk_tool = tool(
        name="hello",
        description="say hello",
        input_schema={"type": "object", "properties": {"name": {"type": "string"}}},
        handler=_handler,
    )
    server = create_sdk_mcp_server({"name": "local", "tools": [sdk_tool]})
    assert is_sdk_server_config(server) is True

    agent = Agent(
        AgentOptions(
            api_key="k",
            api_type="openai-completions",
            model="gpt-4o",
            persist_session=False,
            mcp_servers={"local": server},
        )
    )
    asyncio.run(agent._setup())  # parity check for in-process MCP server support
    assert any(tool_def.name == "mcp__local__hello" for tool_def in agent.tool_pool)


def test_skills_registry_api_surface() -> None:
    sdk.clearSkills()
    sdk.registerSkill(
        {
            "name": "simplify",
            "description": "Simplify code.",
            "aliases": ["simp"],
            "whenToUse": "when code is too complex",
            "userInvocable": True,
        }
    )
    assert sdk.hasSkill("simplify") is True
    assert sdk.hasSkill("simp") is True
    assert sdk.getSkill("simp")["name"] == "simplify"
    assert len(sdk.getAllSkills()) == 1
    assert len(sdk.getUserInvocableSkills()) == 1
    formatted = sdk.formatSkillsForPrompt(200000)
    assert "simplify" in formatted
    assert sdk.unregisterSkill("simplify") is True
    assert sdk.getAllSkills() == []


def test_provider_and_utils_alias_exports_exist() -> None:
    provider = sdk.createProvider("openai-completions", {"apiKey": "k", "baseURL": "https://api.openai.com"})
    assert getattr(provider, "api_type", "") == "openai-completions"
    assert sdk.estimateTokens("abcd") >= 1
    assert sdk.getAutoCompactThreshold("gpt-4o") > 0
    assert sdk.createFileStateCache().size == 0
    assert callable(sdk.createHookRegistry)
    assert callable(sdk.connectMCPServer)


def test_session_camel_case_functions_roundtrip() -> None:
    session_id = f"parity-{uuid.uuid4()}"
    saveSession(
        session_id,
        [{"role": "user", "content": "hello"}],
        {"cwd": ".", "model": "gpt-4o"},
    )
    loaded = loadSession(session_id)
    assert loaded is not None
    assert isinstance(loaded.get("messages"), list)
    assert deleteSession(session_id) is True
