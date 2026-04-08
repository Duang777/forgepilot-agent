from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from forgepilot_api.main import app
from forgepilot_api.storage.repositories import list_messages_by_task


def _read_sse_payloads(response) -> list[dict]:
    payloads = []
    for raw in response.iter_lines():
        if not raw:
            continue
        line = raw.decode() if isinstance(raw, bytes) else raw
        if line.startswith("data: "):
            payloads.append(json.loads(line[6:]))
    return payloads


def test_permission_request_event_is_persisted(monkeypatch) -> None:
    async def _fake_run_agent(*args, **kwargs):
        del args, kwargs
        yield {
            "type": "permission_request",
            "permission": {
                "id": "perm-1",
                "toolName": "Write",
                "input": {"file_path": "a.txt"},
            },
        }
        yield {"type": "done"}

    monkeypatch.setattr("forgepilot_api.api.agent.run_agent", _fake_run_agent)

    client = TestClient(app)
    task_id = "task-permission-persist-1"

    with client.stream("POST", "/agent", json={"prompt": "x", "taskId": task_id}) as resp:
        assert resp.status_code == 200
        payloads = _read_sse_payloads(resp)

    assert payloads[0]["type"] == "permission_request"

    messages = asyncio.run(list_messages_by_task(task_id))
    permission_messages = [m for m in messages if m.get("type") == "permission_request"]

    assert permission_messages
    payload = json.loads(permission_messages[0].get("content") or "{}")
    assert payload.get("id") == "perm-1"
    assert payload.get("toolName") == "Write"

