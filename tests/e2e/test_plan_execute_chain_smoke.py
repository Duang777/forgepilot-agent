from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from fastapi.testclient import TestClient

from forgepilot_api.main import app
from forgepilot_api.services.agent_service import get_plan
from forgepilot_api.storage.repositories import get_task as repo_get_task
from forgepilot_api.storage.repositories import list_messages_by_task


class _FakePlanningAgent:
    def __init__(self, session_id: str) -> None:
        self._session_id = session_id

    async def query(self, prompt: str):
        del prompt
        yield {
            "type": "system",
            "subtype": "init",
            "session_id": self._session_id,
        }
        yield {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            '{"type":"plan","goal":"Deliver feature","steps":['
                            '{"id":"1","description":"Create core module and tests"},'
                            '{"id":"2","description":"Run validation and summarize changes"}'
                            '],"notes":"Keep compatibility"}'
                        ),
                    }
                ],
            },
        }

    async def close(self) -> None:
        return None


class _FakeExecutionAgent:
    def __init__(self, session_id: str) -> None:
        self._session_id = session_id

    async def query(self, prompt: str):
        del prompt
        yield {
            "type": "system",
            "subtype": "init",
            "session_id": self._session_id,
        }
        yield {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Executing approved plan"},
                    {"type": "tool_use", "id": "use-1", "name": "Write", "input": {"file_path": "a.txt"}},
                ],
            },
        }
        yield {
            "type": "tool_result",
            "result": {
                "tool_use_id": "use-1",
                "tool_name": "Write",
                "output": "ok",
                "is_error": False,
            },
        }
        yield {
            "type": "result",
            "subtype": "success",
            "total_cost_usd": 0.2,
            "duration_ms": 80,
        }

    async def close(self) -> None:
        return None


def _read_sse_payloads(response) -> list[dict]:
    payloads = []
    for raw in response.iter_lines():
        if not raw:
            continue
        line = raw.decode() if isinstance(raw, bytes) else raw
        if line.startswith("data: "):
            payloads.append(json.loads(line[6:]))
    return payloads


def test_plan_then_execute_chain_sse_and_persistence(monkeypatch) -> None:
    def _fake_create_agent(options):
        if options.allowed_tools == []:
            return _FakePlanningAgent(str(options.session_id or "plan-sid"))
        return _FakeExecutionAgent(str(options.session_id or "exec-sid"))

    monkeypatch.setattr("forgepilot_api.services.agent_service.create_agent", _fake_create_agent)

    client = TestClient(app)
    task_id = f"task-chain-{uuid4().hex[:8]}"

    with client.stream(
        "POST",
        "/agent/plan",
        json={
            "prompt": "Build this feature",
            "modelConfig": {"apiKey": "k", "model": "gpt-4o", "apiType": "openai-completions"},
        },
    ) as resp:
        assert resp.status_code == 200
        plan_payloads = _read_sse_payloads(resp)

    assert [event["type"] for event in plan_payloads] == ["session", "text", "plan", "done"]
    plan_event = next(event for event in plan_payloads if event["type"] == "plan")
    plan_id = plan_event["plan"]["id"]
    assert get_plan(plan_id) is not None

    with client.stream(
        "POST",
        "/agent/execute",
        json={
            "planId": plan_id,
            "prompt": "Please execute",
            "taskId": task_id,
            "modelConfig": {"apiKey": "k", "model": "gpt-4o", "apiType": "openai-completions"},
        },
    ) as resp:
        assert resp.status_code == 200
        exec_payloads = _read_sse_payloads(resp)

    assert [event["type"] for event in exec_payloads] == [
        "session",
        "text",
        "tool_use",
        "tool_result",
        "result",
        "done",
    ]
    assert get_plan(plan_id) is None

    task = asyncio.run(repo_get_task(task_id))
    assert task is not None
    assert task["status"] == "completed"
    assert float(task["cost"]) == 0.2
    assert int(task["duration"]) == 80

    messages = asyncio.run(list_messages_by_task(task_id))
    assert [msg["type"] for msg in messages] == ["text", "tool_use", "tool_result", "result"]


def test_plan_direct_answer_chain_skips_plan_event(monkeypatch) -> None:
    class _FakeDirectAnswerAgent:
        def __init__(self, session_id: str) -> None:
            self._session_id = session_id

        async def query(self, prompt: str):
            del prompt
            yield {
                "type": "system",
                "subtype": "init",
                "session_id": self._session_id,
            }
            yield {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": '{"type":"direct_answer","answer":"Simple answer"}'}],
                },
            }

        async def close(self) -> None:
            return None

    monkeypatch.setattr(
        "forgepilot_api.services.agent_service.create_agent",
        lambda options: _FakeDirectAnswerAgent(str(options.session_id or "plan-sid")),
    )

    client = TestClient(app)
    with client.stream(
        "POST",
        "/agent/plan",
        json={
            "prompt": "What is 1+1",
            "modelConfig": {"apiKey": "k", "model": "gpt-4o", "apiType": "openai-completions"},
        },
    ) as resp:
        assert resp.status_code == 200
        payloads = _read_sse_payloads(resp)

    assert [event["type"] for event in payloads] == ["session", "text", "direct_answer", "done"]
    assert payloads[2]["content"] == "Simple answer"

