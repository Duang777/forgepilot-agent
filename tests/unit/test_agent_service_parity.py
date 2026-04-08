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

