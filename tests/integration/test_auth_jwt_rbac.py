from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from forgepilot_api.app import create_app
from forgepilot_api.core.settings import reset_settings_cache


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _jwt(payload: dict, *, secret: str, alg: str = "HS256") -> str:
    header = {"typ": "JWT", "alg": alg}
    h = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h}.{p}".encode("ascii")
    hash_fn = hashlib.sha256 if alg == "HS256" else hashlib.sha384 if alg == "HS384" else hashlib.sha512
    sig = _b64url(hmac.new(secret.encode("utf-8"), signing_input, hash_fn).digest())
    return f"{h}.{p}.{sig}"


def _build_client() -> TestClient:
    return TestClient(create_app())


def test_jwt_auth_mode_enforced(monkeypatch) -> None:
    monkeypatch.setenv("FORGEPILOT_AUTH_MODE", "jwt")
    monkeypatch.setenv("FORGEPILOT_JWT_SECRET", "jwt-secret")
    monkeypatch.setenv("FORGEPILOT_AUTH_EXEMPT_PATHS", "/,/health,/metrics")
    reset_settings_cache()
    try:
        client = _build_client()
        unauthorized = client.get("/providers/config")
        assert unauthorized.status_code == 401

        token = _jwt(
            {"sub": "jwt-user", "scope": "providers.read", "exp": int(time.time()) + 300},
            secret="jwt-secret",
        )
        authorized = client.get("/providers/config", headers={"Authorization": f"Bearer {token}"})
        assert authorized.status_code == 200
    finally:
        reset_settings_cache()


def test_combined_auth_accepts_api_key_or_jwt(monkeypatch) -> None:
    monkeypatch.setenv("FORGEPILOT_AUTH_MODE", "api_key_or_jwt")
    monkeypatch.setenv("FORGEPILOT_API_KEYS", "operator:op-key")
    monkeypatch.setenv("FORGEPILOT_JWT_SECRET", "combo-secret")
    monkeypatch.setenv("FORGEPILOT_AUTH_EXEMPT_PATHS", "/,/health,/metrics")
    reset_settings_cache()
    try:
        client = _build_client()
        with_key = client.get("/providers/config", headers={"x-api-key": "op-key"})
        assert with_key.status_code == 200

        token = _jwt({"sub": "jwt-op", "scope": "providers.read", "exp": int(time.time()) + 300}, secret="combo-secret")
        with_jwt = client.get("/providers/config", headers={"Authorization": f"Bearer {token}"})
        assert with_jwt.status_code == 200
    finally:
        reset_settings_cache()


def test_rbac_blocks_without_required_scope(monkeypatch) -> None:
    monkeypatch.setenv("FORGEPILOT_AUTH_MODE", "jwt")
    monkeypatch.setenv("FORGEPILOT_JWT_SECRET", "rbac-secret")
    monkeypatch.setenv("FORGEPILOT_RBAC_ENABLED", "1")
    monkeypatch.setenv("FORGEPILOT_RBAC_DEFAULT_ALLOW", "1")
    monkeypatch.setenv("FORGEPILOT_RBAC_POLICIES", "GET:/providers/config=providers.read")
    monkeypatch.setenv("FORGEPILOT_AUTH_EXEMPT_PATHS", "/,/health,/metrics")
    reset_settings_cache()
    try:
        client = _build_client()

        token_missing = _jwt({"sub": "user-a", "scope": "files.read", "exp": int(time.time()) + 300}, secret="rbac-secret")
        denied = client.get("/providers/config", headers={"Authorization": f"Bearer {token_missing}"})
        assert denied.status_code == 403

        token_ok = _jwt({"sub": "user-b", "scope": "providers.read", "exp": int(time.time()) + 300}, secret="rbac-secret")
        allowed = client.get("/providers/config", headers={"Authorization": f"Bearer {token_ok}"})
        assert allowed.status_code == 200
    finally:
        reset_settings_cache()
