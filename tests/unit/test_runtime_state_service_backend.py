from __future__ import annotations

import asyncio
import uuid

import pytest

from forgepilot_api.core.settings import reset_settings_cache
from forgepilot_api.services import runtime_state_service


def _run(coro):
    return asyncio.run(coro)


def _reset_backend_cache() -> None:
    _run(runtime_state_service.reset_runtime_state_backend_cache())


def test_runtime_state_redis_fail_open_falls_back_to_sqlite(monkeypatch) -> None:
    monkeypatch.setenv("FORGEPILOT_RUNTIME_STATE_BACKEND", "redis")
    monkeypatch.setenv("FORGEPILOT_RUNTIME_STATE_REDIS_URL", "redis://127.0.0.1:1/9")
    monkeypatch.setenv("FORGEPILOT_RUNTIME_STATE_FAIL_OPEN", "1")
    reset_settings_cache()
    _reset_backend_cache()
    try:
        session_id = f"s-{uuid.uuid4().hex}"
        row = _run(runtime_state_service.create_runtime_session(session_id, "plan"))
        assert row["id"] == session_id
        assert row["phase"] == "plan"

        plan = {"id": f"p-{uuid.uuid4().hex}", "goal": "x", "steps": []}
        _run(runtime_state_service.save_runtime_plan(plan, ttl_seconds=30))
        loaded = _run(runtime_state_service.get_runtime_plan(plan["id"]))
        assert loaded is not None
        assert loaded.get("payload", {}).get("id") == plan["id"]
    finally:
        _reset_backend_cache()
        reset_settings_cache()


def test_runtime_state_redis_fail_closed_raises(monkeypatch) -> None:
    monkeypatch.setenv("FORGEPILOT_RUNTIME_STATE_BACKEND", "redis")
    monkeypatch.setenv("FORGEPILOT_RUNTIME_STATE_REDIS_URL", "redis://127.0.0.1:1/9")
    monkeypatch.setenv("FORGEPILOT_RUNTIME_STATE_FAIL_OPEN", "0")
    reset_settings_cache()
    _reset_backend_cache()
    try:
        with pytest.raises(RuntimeError):
            _run(runtime_state_service.create_runtime_session(f"s-{uuid.uuid4().hex}", "execute"))
    finally:
        _reset_backend_cache()
        reset_settings_cache()


def test_runtime_state_sqlite_permission_event_noop(monkeypatch) -> None:
    monkeypatch.setenv("FORGEPILOT_RUNTIME_STATE_BACKEND", "sqlite")
    reset_settings_cache()
    _reset_backend_cache()
    try:
        result = _run(
            runtime_state_service.wait_runtime_permission_event(
                session_id="s1",
                permission_id="p1",
                timeout_seconds=0.1,
            )
        )
        assert result is None
        _run(
            runtime_state_service.publish_runtime_permission_event(
                session_id="s1",
                permission_id="p1",
                status="approved",
            )
        )
    finally:
        _reset_backend_cache()
        reset_settings_cache()
