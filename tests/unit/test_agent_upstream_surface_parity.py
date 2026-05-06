from __future__ import annotations

import asyncio

import forgepilot_sdk.agent as agent_module
from forgepilot_sdk.agent import Agent
from forgepilot_sdk.types import AgentOptions, ThinkingConfig


def _collect(async_gen):
    async def _run():
        out = []
        async for item in async_gen:
            out.append(item)
        return out

    return asyncio.run(_run())


def test_agent_reads_api_type_from_env(monkeypatch) -> None:
    monkeypatch.setenv("DUANGCODE_API_TYPE", "openai-completions")
    monkeypatch.setenv("DUANGCODE_API_KEY", "k")
    agent = Agent(AgentOptions(model="claude-sonnet-4-6", persist_session=False))
    assert agent.getApiType() == "openai-completions"


def test_agent_can_initialize_without_explicit_api_key(monkeypatch) -> None:
    monkeypatch.delenv("DUANGCODE_API_KEY", raising=False)
    monkeypatch.delenv("DUANGCODE_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CODEANY_API_KEY", raising=False)
    monkeypatch.delenv("CODEANY_AUTH_TOKEN", raising=False)
    agent = Agent(
        AgentOptions(
            model="gpt-4o",
            api_type="openai-completions",
            persist_session=False,
        )
    )
    assert agent.get_session_id()


def test_agent_exposes_upstream_style_session_methods(monkeypatch) -> None:
    captured = {"agents": None}

    class _FakeEngine:
        def __init__(self, **kwargs):
            captured["agents"] = kwargs.get("agents")
            self.session_id = kwargs.get("session_id") or "sid"
            self.messages = []
            self.tool_context = type("Ctx", (), {"state": {}})()

        async def submit_message(self, prompt):
            del prompt
            yield {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            }
            yield {
                "type": "result",
                "subtype": "success",
                "session_id": self.session_id,
                "num_turns": 1,
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "total_cost_usd": 0.0,
            }

    monkeypatch.setattr(agent_module, "QueryEngine", _FakeEngine)
    agent = Agent(AgentOptions(api_key="k", api_type="openai-completions", model="gpt-4o", persist_session=False))

    _collect(
        agent.query(
            "hello",
            overrides=AgentOptions(
                persistSession=False,
                agents={"writer": {"description": "Writes responses"}},
            ),
        )
    )
    assert captured["agents"] == {"writer": {"description": "Writes responses"}}

    asyncio.run(agent.setModel("gpt-4.1"))
    assert agent.cfg.model == "gpt-4.1"
    asyncio.run(agent.setPermissionMode("default"))
    assert agent.cfg.permission_mode == "default"
    asyncio.run(agent.setMaxThinkingTokens(256))
    assert isinstance(agent.cfg.thinking, ThinkingConfig)
    assert agent.cfg.thinking.resolved_budget_tokens() == 256
    asyncio.run(agent.setMaxThinkingTokens(None))
    assert agent.cfg.thinking and agent.cfg.thinking.type == "disabled"
    assert agent.getMessages()
    agent.clear()
    assert agent.get_messages() == []
