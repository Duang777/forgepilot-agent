from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient

from forgepilot_api.main import app


def _read_sse_payloads(response) -> list[dict]:
    payloads: list[dict] = []
    for raw in response.iter_lines():
        if not raw:
            continue
        line = raw.decode() if isinstance(raw, bytes) else raw
        if line.startswith("data: "):
            payloads.append(json.loads(line[6:]))
    return payloads


def _metric_value(metrics_text: str, metric_name: str) -> float | None:
    pattern = re.compile(rf"^{re.escape(metric_name)}\s+([0-9.]+)$", re.MULTILINE)
    match = pattern.search(metrics_text)
    if not match:
        return None
    return float(match.group(1))


def test_request_id_is_echoed_back() -> None:
    client = TestClient(app)
    response = client.get("/health", headers={"x-request-id": "req-observe-001"})
    assert response.status_code == 200
    assert response.headers.get("x-request-id") == "req-observe-001"


def test_metrics_endpoint_exposes_counters() -> None:
    client = TestClient(app)
    client.get("/health")
    client.get("/health/dependencies")
    response = client.get("/metrics")
    assert response.status_code == 200

    text = response.text
    assert "forgepilot_http_requests_total" in text
    assert "forgepilot_http_route_requests" in text
    assert 'path="/health"' in text
    assert "forgepilot_sse_streams_started_total" in text
    assert "forgepilot_tool_uses_total" in text
    assert "forgepilot_sandbox_exec_total" in text


def test_metrics_track_tool_and_sse_events(monkeypatch) -> None:
    async def _fake_run_agent(*args, **kwargs):
        del args, kwargs
        yield {"type": "session", "sessionId": "s-observe"}
        yield {"type": "tool_use", "id": "u-1", "name": "Read", "input": {"path": "README.md"}}
        yield {"type": "tool_result", "toolUseId": "u-1", "name": "Read", "output": "fail", "isError": True}
        yield {"type": "done"}

    monkeypatch.setattr("forgepilot_api.api.agent.run_agent", _fake_run_agent)

    client = TestClient(app)
    with client.stream("POST", "/agent", json={"prompt": "x"}) as resp:
        assert resp.status_code == 200
        payloads = _read_sse_payloads(resp)
    assert payloads[-1]["type"] == "done"

    metrics_resp = client.get("/metrics")
    assert metrics_resp.status_code == 200
    text = metrics_resp.text
    assert 'forgepilot_tool_uses_by_name_total{tool="Read"}' in text
    assert 'forgepilot_tool_errors_by_name_total{tool="Read"}' in text
    started = _metric_value(text, "forgepilot_sse_streams_started_total")
    completed = _metric_value(text, "forgepilot_sse_streams_completed_total")
    assert started is not None and started >= 1
    assert completed is not None and completed >= 1


def test_metrics_track_sandbox_fallback_counter() -> None:
    client = TestClient(app)
    run = client.post("/sandbox/run/python", json={"script": "print('metric')", "provider": "native"})
    assert run.status_code == 200
    provider_name = run.json().get("provider")
    assert provider_name

    metrics_resp = client.get("/metrics")
    assert metrics_resp.status_code == 200
    text = metrics_resp.text
    assert f'forgepilot_sandbox_exec_by_provider_total{{provider="{provider_name}"}}' in text
