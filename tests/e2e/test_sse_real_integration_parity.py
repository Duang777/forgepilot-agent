from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from fastapi.testclient import TestClient

import forgepilot_api.services.agent_service as agent_service
from forgepilot_api.main import app
from forgepilot_api.models import ModelConfig
from forgepilot_api.storage.repositories import get_task as repo_get_task
from forgepilot_api.storage.repositories import list_messages_by_task


class _FakeSDKAgent:
    def __init__(self, session_id: str, *, slow_mode: bool = False) -> None:
        self._session_id = session_id
        self._slow_mode = slow_mode

    async def query(self, prompt: str):
        del prompt
        yield {
            "type": "system",
            "subtype": "init",
            "session_id": self._session_id,
        }

        if self._slow_mode:
            await asyncio.sleep(0.2)
            yield {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "still running"}],
                },
            }
            await asyncio.sleep(0.2)
            yield {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "second chunk"}],
                },
            }
            return

        yield {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "tool_use", "id": "u-1", "name": "Read", "input": {"file_path": "a.txt"}},
                ],
            },
        }
        yield {
            "type": "tool_result",
            "result": {
                "tool_use_id": "u-1",
                "tool_name": "Read",
                "output": "ok",
                "is_error": False,
            },
        }
        yield {
            "type": "result",
            "subtype": "success",
            "total_cost_usd": 0.3,
            "duration_ms": 210,
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


def test_agent_sse_real_service_mapping_and_persistence(monkeypatch) -> None:
    def _fake_create_agent(options):
        return _FakeSDKAgent(str(options.session_id or "s-fake"))

    monkeypatch.setattr("forgepilot_api.services.agent_service.create_agent", _fake_create_agent)

    client = TestClient(app)
    task_id = f"task-real-sse-{uuid4().hex[:8]}"

    with client.stream(
        "POST",
        "/agent",
        json={
            "prompt": "x",
            "taskId": task_id,
            "modelConfig": {
                "apiKey": "k",
                "model": "gpt-4o",
                "apiType": "openai-completions",
            },
        },
    ) as resp:
        assert resp.status_code == 200
        payloads = _read_sse_payloads(resp)

    assert [event["type"] for event in payloads] == ["session", "text", "tool_use", "tool_result", "result", "done"]

    task = asyncio.run(repo_get_task(task_id))
    assert task is not None
    assert task["status"] == "completed"
    assert float(task["cost"]) == 0.3
    assert int(task["duration"]) == 210

    messages = asyncio.run(list_messages_by_task(task_id))
    assert [msg["type"] for msg in messages] == ["text", "tool_use", "tool_result", "result"]


def test_execute_sse_real_service_consumes_plan(monkeypatch) -> None:
    def _fake_create_agent(options):
        return _FakeSDKAgent(str(options.session_id or "s-fake"))

    monkeypatch.setattr("forgepilot_api.services.agent_service.create_agent", _fake_create_agent)

    plan_id = f"plan-{uuid4().hex[:8]}"
    agent_service.save_plan(
        {
            "id": plan_id,
            "goal": "Build feature",
            "steps": [{"id": "1", "description": "Implement", "status": "pending"}],
            "notes": "",
            "createdAt": "2026-01-01T00:00:00",
        }
    )

    client = TestClient(app)
    task_id = f"task-exe-real-{uuid4().hex[:8]}"

    with client.stream(
        "POST",
        "/agent/execute",
        json={
            "planId": plan_id,
            "prompt": "do it",
            "taskId": task_id,
            "modelConfig": {
                "apiKey": "k",
                "model": "gpt-4o",
                "apiType": "openai-completions",
            },
        },
    ) as resp:
        assert resp.status_code == 200
        payloads = _read_sse_payloads(resp)

    assert payloads[0]["type"] == "session"
    assert payloads[-1]["type"] == "done"
    assert agent_service.get_plan(plan_id) is None


def test_run_agent_aborts_when_session_flag_is_set(monkeypatch) -> None:
    gate = asyncio.Event()

    class _AbortAwareAgent:
        def __init__(self, session_id: str) -> None:
            self._session_id = session_id

        async def query(self, prompt: str):
            del prompt
            yield {"type": "system", "subtype": "init", "session_id": self._session_id}
            await gate.wait()
            yield {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "after abort"}]},
            }

        async def close(self) -> None:
            return None

    def _fake_create_agent(options):
        return _AbortAwareAgent(str(options.session_id or "s-fake"))

    monkeypatch.setattr("forgepilot_api.services.agent_service.create_agent", _fake_create_agent)

    async def _run() -> list[dict]:
        session = agent_service.create_session("execute")
        stream = agent_service.run_agent(
            "x",
            session,
            model_config=ModelConfig(apiKey="k", model="gpt-4o", apiType="openai-completions"),
        )
        first = await stream.__anext__()
        assert first["type"] == "session"

        session.abort_event.set()
        gate.set()

        second = await stream.__anext__()
        third = await stream.__anext__()
        return [first, second, third]

    events = asyncio.run(_run())
    assert events[1]["type"] == "error"
    assert events[1]["message"] == "Execution aborted"
    assert events[2]["type"] == "done"


def test_stop_route_stops_existing_session() -> None:
    session = agent_service.create_session("execute")
    client = TestClient(app)
    resp = client.post(f"/agent/stop/{session.id}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "stopped"}

