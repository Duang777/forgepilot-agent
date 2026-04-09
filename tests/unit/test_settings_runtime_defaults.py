from __future__ import annotations

from forgepilot_api.core.settings import get_settings, reset_settings_cache


def test_production_cors_defaults_are_restricted(monkeypatch) -> None:
    monkeypatch.setenv("NODE_ENV", "production")
    monkeypatch.delenv("FORGEPILOT_CORS_ORIGINS", raising=False)
    reset_settings_cache()
    try:
        settings = get_settings()
        assert "*" not in settings.cors_origins
        assert "tauri://localhost" in settings.cors_origins
        assert "http://127.0.0.1" in settings.cors_origins
    finally:
        reset_settings_cache()


def test_production_cors_allows_explicit_override(monkeypatch) -> None:
    monkeypatch.setenv("NODE_ENV", "production")
    monkeypatch.setenv("FORGEPILOT_CORS_ORIGINS", "*")
    reset_settings_cache()
    try:
        settings = get_settings()
        assert settings.cors_origins == ["*"]
    finally:
        reset_settings_cache()


def test_runtime_state_backend_config_parsing(monkeypatch) -> None:
    monkeypatch.setenv("FORGEPILOT_RUNTIME_STATE_BACKEND", "REDIS")
    monkeypatch.setenv("FORGEPILOT_RUNTIME_STATE_REDIS_URL", "redis://127.0.0.1:6379/9")
    monkeypatch.setenv("FORGEPILOT_RUNTIME_STATE_REDIS_KEY_PREFIX", "forgepilot:test")
    monkeypatch.setenv("FORGEPILOT_RUNTIME_STATE_FAIL_OPEN", "0")
    reset_settings_cache()
    try:
        settings = get_settings()
        assert settings.runtime_state_backend == "redis"
        assert settings.runtime_state_redis_url == "redis://127.0.0.1:6379/9"
        assert settings.runtime_state_redis_key_prefix == "forgepilot:test"
        assert settings.runtime_state_fail_open is False
    finally:
        reset_settings_cache()
