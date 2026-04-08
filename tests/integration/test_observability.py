from __future__ import annotations

from fastapi.testclient import TestClient

from forgepilot_api.main import app


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
