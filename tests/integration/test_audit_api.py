from __future__ import annotations

from fastapi.testclient import TestClient

from forgepilot_api.app import create_app
from forgepilot_api.core.settings import reset_settings_cache


def _client() -> TestClient:
    return TestClient(create_app())


def test_audit_logs_endpoint_returns_persisted_mutations(monkeypatch) -> None:
    monkeypatch.setenv("FORGEPILOT_AUTH_MODE", "off")
    monkeypatch.setenv("FORGEPILOT_AUDIT_LOG_ENABLED", "1")
    reset_settings_cache()
    try:
        client = _client()
        response = client.post(
            "/agent/permission",
            json={"sessionId": "missing", "permissionId": "audit-q-1", "approved": True},
        )
        assert response.status_code == 200

        logs = client.get("/audit/logs", params={"method": "POST", "path": "/agent/permission", "limit": 10})
        assert logs.status_code == 200
        payload = logs.json()
        assert payload["success"] is True
        assert payload["total"] >= 1
        assert isinstance(payload["items"], list)
        assert any(
            item.get("method") == "POST" and str(item.get("path", "")).startswith("/agent/permission")
            for item in payload["items"]
        )
    finally:
        reset_settings_cache()


def test_audit_logs_protected_when_api_key_enabled(monkeypatch) -> None:
    monkeypatch.setenv("FORGEPILOT_AUTH_MODE", "api_key")
    monkeypatch.setenv("FORGEPILOT_API_KEYS", "auditor:audit-key")
    reset_settings_cache()
    try:
        client = _client()

        denied = client.get("/audit/logs")
        assert denied.status_code == 401

        ok = client.get("/audit/logs", headers={"x-api-key": "audit-key"})
        assert ok.status_code == 200
        assert ok.json()["success"] is True
    finally:
        reset_settings_cache()
