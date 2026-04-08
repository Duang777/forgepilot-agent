from __future__ import annotations

import asyncio
import json
from datetime import datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from forgepilot_api.main import app
from forgepilot_api.services.agent_service import AgentSession
from forgepilot_api.storage.repositories import (
    get_session as repo_get_session,
    get_task as repo_get_task,
    list_messages_by_task,
)


def _read_sse_payloads(response) -> list[dict]:
    payloads = []
    for raw in response.iter_lines():
        if not raw:
            continue
        line = raw.decode() if isinstance(raw, bytes) else raw
        if line.startswith("data: "):
            payloads.append(json.loads(line[6:]))
    return payloads


def test_agent_route_persists_tool_and_result_fields(monkeypatch) -> None:
    async def _fake_run_agent(*args, **kwargs):
        del args, kwargs
        yield {"type": "session", "sessionId": "s-persist-1"}
        yield {"type": "tool_use", "id": "u-1", "name": "Read", "input": {"file_path": "a.txt"}}
        yield {"type": "tool_result", "toolUseId": "u-1", "output": "file content", "isError": False}
        yield {"type": "result", "subtype": "success", "cost": 0.25, "duration": 345, "content": "success"}
        yield {"type": "done"}

    monkeypatch.setattr("forgepilot_api.api.agent.run_agent", _fake_run_agent)

    client = TestClient(app)
    task_id = f"task-persist-fields-{uuid4().hex[:8]}"

    with client.stream("POST", "/agent", json={"prompt": "x", "taskId": task_id}) as resp:
        assert resp.status_code == 200
        payloads = _read_sse_payloads(resp)

    assert [event["type"] for event in payloads] == ["session", "tool_use", "tool_result", "result", "done"]

    task = asyncio.run(repo_get_task(task_id))
    assert task is not None
    assert task["status"] == "completed"
    assert float(task["cost"]) == 0.25
    assert int(task["duration"]) == 345

    messages = asyncio.run(list_messages_by_task(task_id))
    types = [m["type"] for m in messages]
    assert types == ["tool_use", "tool_result", "result"]

    tool_use = messages[0]
    assert tool_use["tool_name"] == "Read"
    assert json.loads(tool_use["tool_input"]) == {"file_path": "a.txt"}
    assert tool_use["tool_use_id"] == "u-1"

    tool_result = messages[1]
    assert tool_result["tool_output"] == "file content"
    assert tool_result["tool_use_id"] == "u-1"
    assert tool_result["error_message"] is None

    session_id = task["session_id"]
    session = asyncio.run(repo_get_session(session_id))
    assert session is not None
    assert session["prompt"] == "x"


def test_agent_route_persists_error_and_task_status(monkeypatch) -> None:
    async def _fake_run_agent(*args, **kwargs):
        del args, kwargs
        yield {"type": "session", "sessionId": "s-persist-err"}
        yield {"type": "error", "message": "boom"}
        yield {"type": "done"}

    monkeypatch.setattr("forgepilot_api.api.agent.run_agent", _fake_run_agent)

    client = TestClient(app)
    task_id = f"task-persist-error-{uuid4().hex[:8]}"

    with client.stream("POST", "/agent", json={"prompt": "x", "taskId": task_id}) as resp:
        assert resp.status_code == 200
        _read_sse_payloads(resp)

    task = asyncio.run(repo_get_task(task_id))
    assert task is not None
    assert task["status"] == "error"

    messages = asyncio.run(list_messages_by_task(task_id))
    assert messages[-1]["type"] == "error"
    assert messages[-1]["error_message"] == "boom"


def test_agent_task_index_increments_for_same_session(monkeypatch) -> None:
    async def _fake_run_agent(*args, **kwargs):
        del args, kwargs
        yield {"type": "text", "content": "working"}
        yield {"type": "done"}

    session_id = f"session-fixed-{uuid4().hex[:8]}"
    task_id_1 = f"task-index-{uuid4().hex[:8]}"
    task_id_2 = f"task-index-{uuid4().hex[:8]}"

    async def _fixed_session(_phase: str = "execute") -> AgentSession:
        return AgentSession(
            id=session_id,
            created_at=datetime.utcnow(),
            phase="execute",
            abort_event=asyncio.Event(),
        )

    monkeypatch.setattr("forgepilot_api.api.agent.run_agent", _fake_run_agent)
    monkeypatch.setattr("forgepilot_api.api.agent.create_session_async", _fixed_session)

    client = TestClient(app)

    with client.stream("POST", "/agent", json={"prompt": "first", "taskId": task_id_1}) as resp1:
        assert resp1.status_code == 200
        _read_sse_payloads(resp1)

    with client.stream("POST", "/agent", json={"prompt": "second", "taskId": task_id_2}) as resp2:
        assert resp2.status_code == 200
        _read_sse_payloads(resp2)

    task1 = asyncio.run(repo_get_task(task_id_1))
    task2 = asyncio.run(repo_get_task(task_id_2))
    session = asyncio.run(repo_get_session(session_id))

    assert task1 is not None and task2 is not None and session is not None
    assert int(task1["task_index"]) == 1
    assert int(task2["task_index"]) == 2
    assert int(session["task_count"]) == 2

