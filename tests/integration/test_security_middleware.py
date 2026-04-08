from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from forgepilot_api.app import create_app
from forgepilot_api.core.settings import reset_settings_cache


def _build_client() -> TestClient:
    return TestClient(create_app())


def test_api_key_auth_enforced_on_non_exempt_routes(monkeypatch) -> None:
    monkeypatch.setenv("FORGEPILOT_AUTH_MODE", "api_key")
    monkeypatch.setenv("FORGEPILOT_API_KEYS", "local:dev-secret-key")
    reset_settings_cache()
    try:
        client = _build_client()

        # Exempt path remains reachable.
        health = client.get("/health")
        assert health.status_code == 200

        unauthorized = client.get("/providers/config")
        assert unauthorized.status_code == 401
        assert unauthorized.json() == {"error": "Unauthorized"}

        wrong_key = client.get("/providers/config", headers={"x-api-key": "wrong"})
        assert wrong_key.status_code == 401

        ok = client.get("/providers/config", headers={"x-api-key": "dev-secret-key"})
        assert ok.status_code == 200
        assert "x-request-id" in ok.headers
    finally:
        reset_settings_cache()


def test_rate_limit_rejects_after_threshold(monkeypatch) -> None:
    monkeypatch.setenv("FORGEPILOT_AUTH_MODE", "off")
    monkeypatch.setenv("FORGEPILOT_RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("FORGEPILOT_RATE_LIMIT_REQUESTS", "2")
    monkeypatch.setenv("FORGEPILOT_RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("FORGEPILOT_AUTH_EXEMPT_PATHS", "/,/health,/metrics")
    reset_settings_cache()
    try:
        client = _build_client()

        first = client.get("/providers/config")
        second = client.get("/providers/config")
        third = client.get("/providers/config")

        assert first.status_code == 200
        assert second.status_code == 200
        assert third.status_code == 429
        assert third.json() == {"error": "Too Many Requests"}
        assert int(third.headers.get("Retry-After", "0")) >= 1

        # Exempt endpoint is never rate-limited.
        health = client.get("/health")
        assert health.status_code == 200
    finally:
        reset_settings_cache()


def test_audit_logs_mutating_requests(monkeypatch, caplog) -> None:
    monkeypatch.setenv("FORGEPILOT_AUTH_MODE", "api_key")
    monkeypatch.setenv("FORGEPILOT_API_KEYS", "auditor:audit-secret")
    monkeypatch.setenv("FORGEPILOT_AUDIT_LOG_ENABLED", "1")
    reset_settings_cache()
    try:
        client = _build_client()
        caplog.set_level(logging.INFO, logger="forgepilot_api.core.security_middleware")

        response = client.post(
            "/agent/permission",
            json={"sessionId": "missing", "permissionId": "p-1", "approved": True},
            headers={"x-api-key": "audit-secret"},
        )
        assert response.status_code == 200

        messages = [record.getMessage() for record in caplog.records]
        assert any("audit actor=auditor method=POST path=/agent/permission status=200" in msg for msg in messages)
    finally:
        reset_settings_cache()
