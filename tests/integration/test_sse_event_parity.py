from __future__ import annotations

import json

from fastapi.testclient import TestClient

from forgepilot_api.main import app


def _read_sse_payloads(response) -> list[dict]:
    payloads = []
    for raw in response.iter_lines():
        if not raw:
            continue
        line = raw.decode() if isinstance(raw, bytes) else raw
        if line.startswith("data: "):
            payloads.append(json.loads(line[6:]))
    return payloads


def test_agent_route_sse_event_order_and_fields(monkeypatch) -> None:
    async def _fake_run_agent(*args, **kwargs):
        del args, kwargs
        yield {"type": "session", "sessionId": "s-1"}
        yield {"type": "tool_use", "id": "u-1", "name": "Read", "input": {"file_path": "a.txt"}}
        yield {"type": "tool_result", "toolUseId": "u-1", "output": "ok", "isError": False}
        yield {"type": "result", "subtype": "success", "cost": 0.01, "duration": 120}
        yield {"type": "done"}

    monkeypatch.setattr("forgepilot_api.api.agent.run_agent", _fake_run_agent)

    client = TestClient(app)
    with client.stream("POST", "/agent", json={"prompt": "x"}) as resp:
        assert resp.status_code == 200
        payloads = _read_sse_payloads(resp)

    assert [p["type"] for p in payloads] == ["session", "tool_use", "tool_result", "result", "done"]
    assert payloads[1]["name"] == "Read"
    assert payloads[2]["toolUseId"] == "u-1"
    assert payloads[3]["subtype"] == "success"


def test_plan_route_emits_plan_event(monkeypatch) -> None:
    async def _fake_run_planning_phase(*args, **kwargs):
        del args, kwargs
        yield {"type": "session", "sessionId": "s-plan"}
        yield {
            "type": "plan",
            "plan": {
                "id": "p1",
                "goal": "Ship feature",
                "steps": [{"id": "1", "description": "Implement", "status": "pending"}],
                "notes": "",
                "createdAt": "2026-01-01T00:00:00",
            },
        }
        yield {"type": "done"}

    monkeypatch.setattr("forgepilot_api.api.agent.run_planning_phase", _fake_run_planning_phase)

    client = TestClient(app)
    with client.stream("POST", "/agent/plan", json={"prompt": "build"}) as resp:
        assert resp.status_code == 200
        payloads = _read_sse_payloads(resp)

    assert [p["type"] for p in payloads] == ["session", "plan", "done"]
    assert payloads[1]["plan"]["id"] == "p1"


def test_execute_route_emits_events_after_plan_lookup(monkeypatch) -> None:
    async def _fake_run_execution_phase(*args, **kwargs):
        del args, kwargs
        yield {"type": "session", "sessionId": "s-exe"}
        yield {"type": "text", "content": "running"}
        yield {"type": "done"}

    async def _fake_get_plan(plan_id: str):
        return {"id": plan_id, "goal": "x", "steps": []}

    monkeypatch.setattr("forgepilot_api.api.agent.run_execution_phase", _fake_run_execution_phase)
    monkeypatch.setattr("forgepilot_api.api.agent.get_plan_async", _fake_get_plan)

    client = TestClient(app)
    with client.stream("POST", "/agent/execute", json={"planId": "p1", "prompt": "go"}) as resp:
        assert resp.status_code == 200
        payloads = _read_sse_payloads(resp)

    assert [p["type"] for p in payloads] == ["session", "text", "done"]
    assert payloads[1]["content"] == "running"


def test_chat_route_emits_text_and_done(monkeypatch) -> None:
    async def _fake_run_chat(*args, **kwargs):
        del args, kwargs
        yield {"type": "text", "content": "hello"}
        yield {"type": "done"}

    monkeypatch.setattr("forgepilot_api.api.agent.run_chat", _fake_run_chat)

    client = TestClient(app)
    with client.stream("POST", "/agent/chat", json={"prompt": "hello"}) as resp:
        assert resp.status_code == 200
        payloads = _read_sse_payloads(resp)

    assert [p["type"] for p in payloads] == ["text", "done"]
    assert payloads[0]["content"] == "hello"

