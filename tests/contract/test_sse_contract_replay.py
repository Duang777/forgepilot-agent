from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from forgepilot_api.main import app

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


def _read_sse_payloads(response) -> list[dict]:
    payloads: list[dict] = []
    for raw in response.iter_lines():
        if not raw:
            continue
        line = raw.decode() if isinstance(raw, bytes) else raw
        if line.startswith("data: "):
            payloads.append(json.loads(line[6:]))
    return payloads


def _load_snapshot(name: str) -> list[dict]:
    return json.loads((SNAPSHOT_DIR / f"{name}.json").read_text(encoding="utf-8"))


def test_plan_sse_replay_snapshot(monkeypatch) -> None:
    async def _fake_run_planning_phase(*args, **kwargs):
        del args, kwargs
        yield {"type": "session", "sessionId": "plan-session-1"}
        yield {
            "type": "plan",
            "plan": {
                "id": "plan-1",
                "goal": "Build feature",
                "steps": [
                    {"id": "1", "description": "Read requirements", "status": "pending"},
                    {"id": "2", "description": "Implement API", "status": "pending"},
                ],
                "notes": "Keep API stable.",
                "createdAt": "2026-04-08T00:00:00",
            },
        }
        yield {"type": "done"}

    monkeypatch.setattr("forgepilot_api.api.agent.run_planning_phase", _fake_run_planning_phase)
    client = TestClient(app)
    with client.stream("POST", "/agent/plan", json={"prompt": "Do it"}) as resp:
        assert resp.status_code == 200
        payloads = _read_sse_payloads(resp)
    assert payloads == _load_snapshot("agent_plan_stream")


def test_execute_sse_replay_snapshot(monkeypatch) -> None:
    async def _fake_get_plan(plan_id: str):
        return {"id": plan_id, "goal": "Build feature", "steps": []}

    async def _fake_run_execution_phase(*args, **kwargs):
        del args, kwargs
        yield {"type": "session", "sessionId": "exe-session-1"}
        yield {"type": "text", "content": "running"}
        yield {"type": "tool_use", "id": "u-1", "name": "Read", "input": {"path": "README.md"}}
        yield {"type": "tool_result", "toolUseId": "u-1", "name": "Read", "output": "ok", "isError": False}
        yield {"type": "result", "subtype": "success", "cost": 0.05, "duration": 321}
        yield {"type": "done"}

    monkeypatch.setattr("forgepilot_api.api.agent.get_plan_async", _fake_get_plan)
    monkeypatch.setattr("forgepilot_api.api.agent.run_execution_phase", _fake_run_execution_phase)
    client = TestClient(app)
    with client.stream("POST", "/agent/execute", json={"planId": "plan-1", "prompt": "Go"}) as resp:
        assert resp.status_code == 200
        payloads = _read_sse_payloads(resp)
    assert payloads == _load_snapshot("agent_execute_stream")


def test_agent_sse_replay_snapshot(monkeypatch) -> None:
    async def _fake_run_agent(*args, **kwargs):
        del args, kwargs
        yield {"type": "session", "sessionId": "agent-session-1"}
        yield {"type": "tool_use", "id": "u-2", "name": "Write", "input": {"path": "main.py"}}
        yield {"type": "tool_result", "toolUseId": "u-2", "name": "Write", "output": "denied", "isError": True}
        yield {"type": "error", "message": "permission denied"}
        yield {"type": "done"}

    monkeypatch.setattr("forgepilot_api.api.agent.run_agent", _fake_run_agent)
    client = TestClient(app)
    with client.stream("POST", "/agent", json={"prompt": "Do it"}) as resp:
        assert resp.status_code == 200
        payloads = _read_sse_payloads(resp)
    assert payloads == _load_snapshot("agent_stream")
