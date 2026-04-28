from __future__ import annotations

import asyncio
from pathlib import Path

import forgepilot_sdk.agent as agent_module
import forgepilot_sdk.engine as engine_module
from forgepilot_sdk.agent import Agent
from forgepilot_sdk.engine import QueryEngine
from forgepilot_sdk.providers.base import ProviderResponse, ProviderToolCall
from forgepilot_sdk.tools.base import define_tool
from forgepilot_sdk.types import AgentOptions, ConversationMessage, ToolContext, ToolResult


def _collect(async_gen):
    async def _run():
        out = []
        async for item in async_gen:
            out.append(item)
        return out

    return asyncio.run(_run())


class _SimpleProvider:
    api_type = "openai-completions"

    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0

    async def create_message(self, **kwargs):  # type: ignore[override]
        del kwargs
        if self._idx >= len(self._responses):
            return ProviderResponse(content="done", tool_calls=[], usage={"input_tokens": 1, "output_tokens": 1})
        item = self._responses[self._idx]
        self._idx += 1
        return item


def test_engine_can_use_tool_exception_returns_tool_result_error() -> None:
    provider = _SimpleProvider(
        [
            ProviderResponse(
                content="",
                tool_calls=[ProviderToolCall(id="u1", name="Bash", input={"command": "echo hi"})],
                usage={"input_tokens": 1, "output_tokens": 1},
            ),
            ProviderResponse(content="done", tool_calls=[], usage={"input_tokens": 1, "output_tokens": 1}),
        ]
    )

    async def _tool_call(input_data: dict, _ctx: ToolContext) -> ToolResult:
        return ToolResult(content=str(input_data), is_error=False)

    async def _can_use_tool(_tool, _input):
        raise RuntimeError("policy backend down")

    tool = define_tool(
        name="Bash",
        description="Run command",
        input_schema={"type": "object", "properties": {"command": {"type": "string"}}},
        call=_tool_call,
    )

    engine = QueryEngine(
        provider=provider,  # type: ignore[arg-type]
        model="gpt-4o",
        tools=[tool],
        cwd=Path.cwd(),
        can_use_tool=_can_use_tool,
        max_turns=3,
    )
    events = _collect(engine.submit_message("run"))
    tool_results = [e for e in events if e.get("type") == "tool_result"]
    assert tool_results
    assert tool_results[0]["result"]["is_error"] is True
    assert "Permission check error" in str(tool_results[0]["result"]["output"])


def test_engine_policy_deny_returns_policy_marker_tool_result(tmp_path) -> None:
    provider = _SimpleProvider(
        [
            ProviderResponse(
                content="",
                tool_calls=[ProviderToolCall(id="u-policy-deny", name="Bash", input={"command": "rm -rf ./tmp"})],
                usage={"input_tokens": 1, "output_tokens": 1},
            ),
            ProviderResponse(content="done", tool_calls=[], usage={"input_tokens": 1, "output_tokens": 1}),
        ]
    )

    executed = {"called": False}

    async def _tool_call(input_data: dict, _ctx: ToolContext) -> ToolResult:
        del input_data
        executed["called"] = True
        return ToolResult(content="ok", is_error=False)

    tool = define_tool(
        name="Bash",
        description="Run command",
        input_schema={"type": "object", "properties": {"command": {"type": "string"}}},
        call=_tool_call,
    )

    engine = QueryEngine(
        provider=provider,  # type: ignore[arg-type]
        model="gpt-4o",
        tools=[tool],
        cwd=tmp_path,
        max_turns=3,
    )
    events = _collect(engine.submit_message("run"))
    tool_results = [e for e in events if e.get("type") == "tool_result"]
    assert tool_results
    assert executed["called"] is False
    assert tool_results[0]["result"]["is_error"] is True
    assert "__POLICY_DENIED__" in str(tool_results[0]["result"]["output"])


def test_engine_policy_requires_permission_before_execution(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FORGEPILOT_POLICY_DEV_RELAXED", "0")

    provider = _SimpleProvider(
        [
            ProviderResponse(
                content="",
                tool_calls=[ProviderToolCall(id="u-policy-perm", name="Bash", input={"command": "git push origin main"})],
                usage={"input_tokens": 1, "output_tokens": 1},
            ),
            ProviderResponse(content="done", tool_calls=[], usage={"input_tokens": 1, "output_tokens": 1}),
        ]
    )

    executed = {"called": False}
    requested: list[dict[str, str]] = []

    async def _tool_call(input_data: dict, _ctx: ToolContext) -> ToolResult:
        del input_data
        executed["called"] = True
        return ToolResult(content="ok", is_error=False)

    async def _on_permission(permission: dict) -> None:
        requested.append(permission)

    async def _wait(_permission_id: str) -> bool:
        return False

    tool = define_tool(
        name="Bash",
        description="Run command",
        input_schema={"type": "object", "properties": {"command": {"type": "string"}}},
        call=_tool_call,
        read_only=False,
        concurrency_safe=False,
    )

    engine = QueryEngine(
        provider=provider,  # type: ignore[arg-type]
        model="gpt-4o",
        tools=[tool],
        cwd=tmp_path,
        max_turns=3,
        on_permission_request=_on_permission,
        wait_for_permission_decision=_wait,
    )
    events = _collect(engine.submit_message("run"))

    system_subtypes = [e.get("subtype") for e in events if e.get("type") == "system"]
    tool_results = [e for e in events if e.get("type") == "tool_result"]

    assert "permission_request" in system_subtypes
    assert requested
    assert executed["called"] is False
    assert tool_results
    assert "Permission denied" in str(tool_results[0]["result"]["output"])


def test_engine_include_partial_messages_emits_partial_blocks() -> None:
    provider = _SimpleProvider(
        [
            ProviderResponse(content="hello world", tool_calls=[], usage={"input_tokens": 1, "output_tokens": 1}),
        ]
    )
    engine = QueryEngine(
        provider=provider,  # type: ignore[arg-type]
        model="gpt-4o",
        tools=[],
        cwd=Path.cwd(),
        include_partial_messages=True,
    )
    events = _collect(engine.submit_message("hi"))
    partials = [e for e in events if e.get("type") == "partial_message"]
    assert partials
    assert partials[0]["partial"]["type"] == "text"


def test_engine_can_use_tool_accepts_updated_input_snake_case() -> None:
    observed = {"input": None}
    provider = _SimpleProvider(
        [
            ProviderResponse(
                content="",
                tool_calls=[ProviderToolCall(id="u2", name="Write", input={"path": "a.txt"})],
                usage={"input_tokens": 1, "output_tokens": 1},
            ),
            ProviderResponse(content="done", tool_calls=[], usage={"input_tokens": 1, "output_tokens": 1}),
        ]
    )

    async def _tool_call(input_data: dict, _ctx: ToolContext) -> ToolResult:
        observed["input"] = input_data
        return ToolResult(content="ok", is_error=False)

    async def _can_use_tool(_tool, _input):
        return {"behavior": "allow", "updated_input": {"path": "b.txt"}}

    tool = define_tool(
        name="Write",
        description="Write file",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        call=_tool_call,
        read_only=False,
    )

    engine = QueryEngine(
        provider=provider,  # type: ignore[arg-type]
        model="gpt-4o",
        tools=[tool],
        cwd=Path.cwd(),
        can_use_tool=_can_use_tool,
    )
    _collect(engine.submit_message("write"))
    assert observed["input"] == {"path": "b.txt"}


def test_agent_query_override_can_reset_to_default_max_turns(monkeypatch) -> None:
    captured = {"max_turns": None}

    class _FakeEngine:
        def __init__(self, **kwargs):
            captured["max_turns"] = kwargs.get("max_turns")
            self.session_id = kwargs.get("session_id") or "sid"
            self.messages = []
            self.tool_context = type("Ctx", (), {"state": {}})()

        async def submit_message(self, prompt):
            del prompt
            yield {"type": "result", "subtype": "success", "num_turns": 1, "usage": {"input_tokens": 0, "output_tokens": 0}}

    monkeypatch.setattr(agent_module, "QueryEngine", _FakeEngine)
    opts = AgentOptions(
        api_key="k",
        api_type="openai-completions",
        model="gpt-4o",
        max_turns=50,
        persist_session=False,
    )
    agent = Agent(opts)
    events = _collect(agent.query("x", overrides=AgentOptions(maxTurns=20, persistSession=False)))
    assert events
    assert captured["max_turns"] == 20


def test_agent_prompt_messages_are_message_log_not_raw_events(monkeypatch) -> None:
    class _FakeEngine:
        def __init__(self, **kwargs):
            self.session_id = kwargs.get("session_id") or "sid"
            self.messages = []
            self.tool_context = type("Ctx", (), {"state": {}})()

        async def submit_message(self, prompt):
            del prompt
            yield {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
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
    agent = Agent(
        AgentOptions(api_key="k", api_type="openai-completions", model="gpt-4o", persist_session=False)
    )
    result = asyncio.run(agent.prompt("hello"))
    assert result.messages
    assert result.messages[0]["type"] == "assistant"
    assert result.messages[-1]["type"] == "user"


def test_engine_includes_subagents_in_system_prompt() -> None:
    captured = {"system_prompt": ""}

    class _Provider:
        api_type = "openai-completions"

        async def create_message(self, **kwargs):  # type: ignore[override]
            captured["system_prompt"] = str(kwargs.get("system_prompt") or "")
            return ProviderResponse(content="done", tool_calls=[], usage={"input_tokens": 1, "output_tokens": 1})

    engine = QueryEngine(
        provider=_Provider(),  # type: ignore[arg-type]
        model="gpt-4o",
        tools=[],
        cwd=Path.cwd(),
        agents={"writer": {"description": "Writes files"}},
    )
    events = _collect(engine.submit_message("hi"))
    assert events
    assert "# Available Subagents" in captured["system_prompt"]
    assert "writer" in captured["system_prompt"]


def test_engine_empty_provider_response_emits_error_result() -> None:
    class _Provider:
        api_type = "openai-completions"

        async def create_message(self, **kwargs):  # type: ignore[override]
            del kwargs
            return ProviderResponse(content="", tool_calls=[], usage={"input_tokens": 1, "output_tokens": 1})

    engine = QueryEngine(
        provider=_Provider(),  # type: ignore[arg-type]
        model="gpt-4o",
        tools=[],
        cwd=Path.cwd(),
    )
    events = _collect(engine.submit_message("hi"))
    result = [e for e in events if e.get("type") == "result"]
    assert result
    assert result[-1]["subtype"] == "error_during_execution"


def test_engine_emits_compact_boundary_event(monkeypatch) -> None:
    class _Provider:
        api_type = "openai-completions"

        async def create_message(self, **kwargs):  # type: ignore[override]
            del kwargs
            return ProviderResponse(content="done", tool_calls=[], usage={"input_tokens": 1, "output_tokens": 1})

    async def _apply(*args, **kwargs):
        del args, kwargs
        return {
            "messages": [
                ConversationMessage(role="user", content="summary"),
                ConversationMessage(role="assistant", content="ack"),
            ],
            "summary": "conversation summary",
            "compact_state": engine_module.create_auto_compact_state(),
        }

    monkeypatch.setattr(engine_module.ContextOrchestrator, "apply_before_model_call", _apply)

    engine = QueryEngine(
        provider=_Provider(),  # type: ignore[arg-type]
        model="gpt-4o",
        tools=[],
        cwd=Path.cwd(),
    )
    events = _collect(engine.submit_message("hi"))
    compact_events = [e for e in events if e.get("type") == "system" and e.get("subtype") == "compact_boundary"]
    assert compact_events
    assert compact_events[0]["summary"] == "conversation summary"
