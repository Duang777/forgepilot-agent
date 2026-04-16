from __future__ import annotations

import asyncio

import forgepilot_api.services.agent_service as agent_service
from forgepilot_api.models import ModelConfig


def _collect(async_gen):
    async def _run():
        out = []
        async for item in async_gen:
            out.append(item)
        return out

    return asyncio.run(_run())


def test_parse_planning_response_plan_json() -> None:
    text = """
```json
{
  "type": "plan",
  "goal": "Create report",
  "steps": [
    {"id":"1","description":"Read source data"},
    {"id":"2","description":"Generate report output"}
  ],
  "notes": "Keep it short"
}
```
"""
    parsed = agent_service._parse_planning_response(text)
    assert parsed is not None
    assert parsed["type"] == "plan"
    assert parsed["plan"]["goal"] == "Create report"
    assert len(parsed["plan"]["steps"]) == 2


def test_parse_planning_response_direct_answer_fallback() -> None:
    parsed = agent_service._parse_planning_response("hello, I can help directly")
    assert parsed is not None
    assert parsed["type"] == "direct_answer"
    assert "help directly" in parsed["answer"]


def test_get_session_work_dir_rules() -> None:
    custom = agent_service._get_session_work_dir("C:\\tmp\\work\\sessions\\abc", "x", "t1")
    assert custom.endswith("sessions\\abc")
    generated = agent_service._get_session_work_dir("C:\\tmp\\work", "Build parser", "task-123456")
    assert "sessions" in generated.lower()
    assert "123456" in generated


def test_sanitize_custom_api_error() -> None:
    cfg = ModelConfig(apiKey="k", baseUrl="https://api.example.com", model="gpt-4o", apiType="openai-completions")
    message = agent_service._sanitize_error("random internal failure", cfg)
    assert message.startswith("__CUSTOM_API_ERROR__|https://api.example.com|")


def test_run_planning_phase_without_model(monkeypatch) -> None:
    async def _fake_provider_config():
        return {"agent": {"config": {}}, "defaultModel": ""}

    monkeypatch.setattr(agent_service, "load_codex_runtime_config", lambda: {})
    monkeypatch.setattr(agent_service, "get_provider_config", _fake_provider_config)
    session = agent_service.create_session("plan")
    events = _collect(agent_service.run_planning_phase("hello", session, model_config=None))
    errors = [event for event in events if event.get("type") == "error"]
    assert errors
    assert errors[0]["message"] == "__MODEL_NOT_CONFIGURED__"
    assert events[-1]["type"] == "done"


def test_run_execution_phase_missing_plan() -> None:
    session = agent_service.create_session("execute")
    events = _collect(
        agent_service.run_execution_phase(
            "missing-plan-id",
            session,
            original_prompt="build feature",
        )
    )
    assert events[0]["type"] == "error"
    assert "Plan not found" in events[0]["message"]
    assert events[-1]["type"] == "done"


def test_permission_lifecycle() -> None:
    async def _flow():
        session = agent_service.create_session("execute")
        permission = {"id": "perm-1", "toolName": "Bash", "input": {"command": "ls"}}
        await agent_service._register_permission_request(session.id, permission)

        assert await agent_service.respond_to_permission_async(session.id, "perm-1", True) is True
        decision = await agent_service._wait_for_permission_decision(session.id, "perm-1")
        assert decision is True

        # unknown permission should return False
        assert await agent_service.respond_to_permission_async(session.id, "perm-x", True) is False
        await agent_service.delete_session_async(session.id)

    asyncio.run(_flow())


def test_get_plan_async_uses_runtime_as_source_of_truth(monkeypatch) -> None:
    plan_id = "plan-stale-local-cache"
    stale_plan = {"id": plan_id, "goal": "stale", "steps": []}
    agent_service._local_plans[plan_id] = stale_plan

    async def _fake_get_runtime_plan(_plan_id: str):
        return None

    monkeypatch.setattr(agent_service, "get_runtime_plan", _fake_get_runtime_plan)
    result = asyncio.run(agent_service.get_plan_async(plan_id))
    assert result is None
    assert plan_id not in agent_service._local_plans


def test_resolve_model_config_falls_back_to_codex_runtime(monkeypatch) -> None:
    async def _fake_provider_config():
        return {"agent": {"config": {}}, "defaultModel": ""}

    monkeypatch.setattr(agent_service, "get_provider_config", _fake_provider_config)
    monkeypatch.setattr(
        agent_service,
        "load_codex_runtime_config",
        lambda: {
            "apiKey": "codex-key",
            "baseUrl": "https://codex.example.com",
            "model": "gpt-5.4",
            "apiType": "openai-completions",
        },
    )

    resolved = asyncio.run(agent_service._resolve_model_config(None))
    assert resolved is not None
    assert resolved.apiKey == "codex-key"
    assert resolved.baseUrl == "https://codex.example.com"
    assert resolved.model == "gpt-5.4"
    assert resolved.apiType == "openai-completions"


def test_agent_service_camel_case_aliases_for_session_and_plan() -> None:
    session = agent_service.createSession("execute")
    assert session.id
    assert agent_service.getSession(session.id) is not None
    plan = {"id": "plan-camel-1", "goal": "x", "steps": []}
    agent_service.savePlan(plan)
    assert agent_service.getPlan("plan-camel-1") == plan
    assert agent_service.deletePlan("plan-camel-1") is True
    agent_service.stopAgent(session.id)
    asyncio.run(agent_service.delete_session_async(session.id))


def test_run_agent_camel_case_wrapper_delegates(monkeypatch) -> None:
    async def _fake_run_agent(*args, **kwargs):
        del args, kwargs
        yield {"type": "text", "content": "ok"}
        yield {"type": "done"}

    monkeypatch.setattr(agent_service, "run_agent", _fake_run_agent)

    async def _collect():
        session = agent_service.createSession("execute")
        out = []
        async for event in agent_service.runAgent("hello", session):
            out.append(event)
        return out

    events = asyncio.run(_collect())
    assert events == [{"type": "text", "content": "ok"}, {"type": "done"}]


def test_workspace_instruction_allows_creating_new_files() -> None:
    text = agent_service._get_workspace_instruction("C:\\work", sandbox_enabled=False)
    assert "new files can be created directly" in text
    assert "execute directly with tools" in text
    assert "Use Read before Write even for new files" not in text


def test_task_request_detection_prefers_execution_for_file_work() -> None:
    assert agent_service._looks_like_task_request("帮我生成一个 html 页面并写入 index.html")
    assert agent_service._looks_like_task_request("Please create README.md and update setup.py")
    assert not agent_service._looks_like_task_request("你好，你是谁？")


def test_build_fallback_plan_from_prompt_is_structured() -> None:
    plan = agent_service._build_fallback_plan_from_prompt("Create index.html with a clean landing page")
    assert isinstance(plan.get("id"), str) and plan["id"]
    assert "goal" in plan and plan["goal"]
    assert isinstance(plan.get("steps"), list) and len(plan["steps"]) == 3
    assert plan["steps"][0]["status"] == "pending"


def test_run_planning_phase_converts_direct_answer_to_plan_for_task_prompt(monkeypatch) -> None:
    async def _fake_resolve_model_config(_model_config):
        return ModelConfig(
            apiKey="test-key",
            baseUrl="https://example.invalid",
            model="gpt-5.4",
            apiType="openai-completions",
        )

    class _FakeAgent:
        async def query(self, _prompt: str):
            yield {"type": "system", "subtype": "init", "session_id": "session-1"}
            yield {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": '{"type":"direct_answer","answer":"Please confirm first."}',
                        }
                    ]
                },
            }

        async def close(self):
            return None

    monkeypatch.setattr(agent_service, "_resolve_model_config", _fake_resolve_model_config)
    monkeypatch.setattr(agent_service, "create_agent", lambda _options: _FakeAgent())

    async def _collect_events():
        session = agent_service.create_session("plan")
        out = []
        async for event in agent_service.run_planning_phase(
            "请创建一个 index.html 并保存到工作目录",
            session,
        ):
            out.append(event)
        return out

    events = asyncio.run(_collect_events())
    assert any(event.get("type") == "plan" for event in events)
    assert not any(event.get("type") == "direct_answer" for event in events if "Please confirm first." in str(event))


def test_format_plan_for_execution_enforces_tool_execution() -> None:
    plan = {
        "goal": "Create index.html",
        "steps": [{"id": "1", "description": "Create file"}],
        "notes": "none",
    }
    text = agent_service._format_plan_for_execution(
        plan,
        "C:\\work",
        sandbox_enabled=False,
        language="en-US",
        original_prompt="Create index.html",
    )
    assert "Do not ask for additional confirmation" in text
    assert "Perform real tool calls" in text
    assert "include absolute paths" in text


def test_should_block_unverified_file_success_when_no_tool_calls() -> None:
    prompt = "帮我生成一个 html 页面并保存为 index.html"
    assert agent_service._should_block_unverified_file_success(
        prompt,
        set(),
        "已为你生成在以下路径：C:\\Users\\x\\index.html",
    )


def test_should_not_block_file_success_when_write_tool_present() -> None:
    prompt = "Create index.html file"
    assert not agent_service._should_block_unverified_file_success(
        prompt,
        {"Write"},
        "Created file at C:\\work\\index.html",
    )


def test_run_agent_blocks_fake_file_success(monkeypatch) -> None:
    async def _fake_resolve_model_config(_model_config):
        return ModelConfig(
            apiKey="test-key",
            baseUrl="https://example.invalid",
            model="gpt-5.4",
            apiType="openai-completions",
        )

    class _FakeAgent:
        async def query(self, _prompt: str):
            yield {"type": "system", "subtype": "init", "session_id": "session-1"}
            yield {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "已为你生成在以下路径：C:\\\\Users\\\\13087\\\\.forgepilot\\\\sessions\\\\a\\\\index.html",
                        }
                    ]
                },
            }
            yield {"type": "result", "subtype": "success", "total_cost_usd": 0.0, "duration_ms": 10}

        async def close(self):
            return None

    monkeypatch.setattr(agent_service, "_resolve_model_config", _fake_resolve_model_config)
    monkeypatch.setattr(agent_service, "create_agent", lambda _options: _FakeAgent())

    async def _collect_events():
        session = agent_service.create_session("execute")
        out = []
        async for event in agent_service.run_agent(
            "帮我生成一个html页面并告诉我路径",
            session,
            work_dir="C:\\work",
        ):
            out.append(event)
        return out

    events = asyncio.run(_collect_events())
    assert any(event.get("type") == "error" and event.get("message") == "__UNVERIFIED_FILE_OPERATION__" for event in events)
    assert any(event.get("type") == "result" and event.get("subtype") == "error" for event in events)

