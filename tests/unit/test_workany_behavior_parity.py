from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from forgepilot_api.sandbox.registry import SandboxRegistry
from forgepilot_api.services import preview_service, provider_service
from forgepilot_api.services.preview_service import PreviewInstance, PreviewManager


class _FakeSandboxProvider:
    def __init__(self, available: bool = True) -> None:
        self._available = available
        self.inits: list[dict[str, Any] | None] = []
        self.stop_calls = 0

    async def is_available(self) -> bool:
        return self._available

    async def init(self, config: dict[str, Any] | None = None) -> None:
        self.inits.append(config)

    async def stop(self) -> None:
        self.stop_calls += 1

    async def shutdown(self) -> None:
        self.stop_calls += 1


def test_sandbox_registry_reuses_instance_with_same_config_and_recreates_on_change() -> None:
    async def _run() -> None:
        created: list[_FakeSandboxProvider] = []

        def _factory(_cfg: dict[str, Any] | None = None) -> _FakeSandboxProvider:
            provider = _FakeSandboxProvider()
            created.append(provider)
            return provider

        registry = SandboxRegistry()
        registry.register("fake", _factory)

        first = await registry.get_instance("fake", {"config": {"a": 1}})
        again = await registry.get_instance("fake", {"config": {"a": 1}})
        assert first is again

        changed = await registry.get_instance("fake", {"config": {"a": 2}})
        assert changed is not first
        assert first.stop_calls >= 1
        assert len(created) == 2

    asyncio.run(_run())


def test_sandbox_registry_best_available_follows_priority() -> None:
    async def _run() -> None:
        registry = SandboxRegistry()
        registry.register("native", lambda _cfg=None: _FakeSandboxProvider(available=True))
        registry.register("codex", lambda _cfg=None: _FakeSandboxProvider(available=False))
        registry.register("claude", lambda _cfg=None: _FakeSandboxProvider(available=True))
        assert await registry.get_best_available() == "claude"

    asyncio.run(_run())


class _FakeActiveProvider:
    def __init__(self, provider_type: str) -> None:
        self.provider_type = provider_type
        self.shutdown_calls = 0

    async def shutdown(self) -> None:
        self.shutdown_calls += 1


class _FakeRegistry:
    def __init__(self, available: list[str]) -> None:
        self._available = available
        self.instances: dict[str, _FakeActiveProvider] = {}
        self.requests: list[tuple[str, dict[str, Any] | None]] = []

    async def get_available(self) -> list[str]:
        return list(self._available)

    def get_all_metadata(self) -> list[dict[str, Any]]:
        return [{"type": p, "name": p, "description": p} for p in self._available]

    async def get_instance(self, provider_type: str, config: dict[str, Any] | None = None) -> _FakeActiveProvider:
        self.requests.append((provider_type, config))
        provider = _FakeActiveProvider(provider_type)
        self.instances[provider_type] = provider
        return provider

    async def stop_all(self) -> None:
        return None


def test_provider_manager_switch_shuts_down_previous_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run() -> None:
        async def _fake_write_setting(_key: str, _value: Any) -> None:
            return None

        monkeypatch.setattr(provider_service, "write_setting", _fake_write_setting)
        manager = provider_service.ProviderManager()
        sandbox_registry = _FakeRegistry(["native", "codex"])
        agent_registry = _FakeRegistry(["codeany"])
        manager.register_registry("sandbox", sandbox_registry)
        manager.register_registry("agent", agent_registry)
        manager._initialized = True

        await manager.switch_sandbox_provider("native", {"x": 1})
        first = manager._active_providers["sandbox"]
        await manager.switch_sandbox_provider("codex", {"x": 2})
        assert first.shutdown_calls == 1

        cfg = manager.get_config()
        assert cfg["sandbox"]["type"] == "codex"
        assert cfg["sandbox"]["config"] == {"x": 2}

    asyncio.run(_run())


def test_provider_manager_initialization_seeds_agent_config_from_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run() -> None:
        async def _fake_read_settings() -> dict[str, Any]:
            return {}

        async def _fake_write_setting(_key: str, _value: Any) -> None:
            return None

        monkeypatch.setattr(provider_service, "read_settings", _fake_read_settings)
        monkeypatch.setattr(provider_service, "write_setting", _fake_write_setting)
        monkeypatch.setattr(
            provider_service,
            "load_codex_runtime_config",
            lambda: {
                "apiKey": "codex-key",
                "baseUrl": "https://codex.example.com",
                "model": "gpt-5.4",
                "apiType": "openai-completions",
            },
        )

        manager = provider_service.ProviderManager()
        await manager.initialize()
        cfg = manager.get_config()
        assert cfg["agent"]["config"]["apiKey"] == "codex-key"
        assert cfg["agentConfigSource"] == "codex"

    asyncio.run(_run())


def test_provider_manager_respects_environment_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run() -> None:
        async def _fake_read_settings() -> dict[str, Any]:
            return {
                "sandboxProvider": "",
                "agentProvider": "",
            }

        async def _fake_write_setting(_key: str, _value: Any) -> None:
            return None

        monkeypatch.setenv("SANDBOX_PROVIDER", "native")
        monkeypatch.setenv("AGENT_PROVIDER", "custom")
        monkeypatch.setattr(provider_service, "read_settings", _fake_read_settings)
        monkeypatch.setattr(provider_service, "write_setting", _fake_write_setting)
        monkeypatch.setattr(provider_service, "load_codex_runtime_config", lambda: {})

        manager = provider_service.ProviderManager()
        await manager.initialize()
        cfg = manager.get_config()
        assert cfg["sandbox"]["type"] == "native"
        assert cfg["agent"]["type"] == "custom"

    asyncio.run(_run())


def test_provider_manager_camel_case_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run() -> None:
        async def _fake_write_setting(_key: str, _value: Any) -> None:
            return None

        monkeypatch.setattr(provider_service, "write_setting", _fake_write_setting)
        manager = provider_service.ProviderManager()
        sandbox_registry = _FakeRegistry(["native"])
        agent_registry = _FakeRegistry(["codeany"])
        manager.registerRegistry("sandbox", sandbox_registry)
        manager.registerRegistry("agent", agent_registry)
        manager._initialized = True

        await manager.switchSandboxProvider("native", {"foo": "bar"})
        await manager.switchAgentProvider("codeany", {"k": "v"})
        cfg = manager.getConfig()
        assert cfg["sandbox"]["type"] == "native"
        assert cfg["agent"]["type"] == "codeany"

        sandbox_available = await manager.getAvailableSandboxProviders()
        agent_available = await manager.getAvailableAgentProviders()
        assert sandbox_available == ["native"]
        assert agent_available == ["codeany"]

    asyncio.run(_run())


def test_preview_manager_returns_existing_running_instance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def _run() -> None:
        manager = PreviewManager()
        now = datetime.now(timezone.utc)
        manager._instances["task-1"] = PreviewInstance(
            id="preview-task-1",
            task_id="task-1",
            work_dir=tmp_path,
            port=5173,
            status="running",
            started_at=now,
            last_accessed_at=now - timedelta(minutes=5),
        )
        manager._used_ports.add(5173)
        monkeypatch.setattr(preview_service, "is_node_available", lambda: False)
        status = await manager.start_preview("task-1", str(tmp_path), 5173)
        assert status["status"] == "running"
        assert status["hostPort"] == 5173

    asyncio.run(_run())


def test_preview_manager_errors_when_node_unavailable_for_new_instance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def _run() -> None:
        manager = PreviewManager()
        monkeypatch.setattr(preview_service, "is_node_available", lambda: False)
        status = await manager.start_preview("task-new", str(tmp_path))
        assert status["status"] == "error"
        assert "Node.js/npm is not available" in str(status.get("error"))

    asyncio.run(_run())


def test_preview_manager_status_for_unknown_task_is_stopped() -> None:
    manager = PreviewManager()
    status = manager.get_status("missing-task")
    assert status["status"] == "stopped"
    assert status["taskId"] == "missing-task"


def test_preview_manager_camel_case_methods(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def _run() -> None:
        manager = PreviewManager()
        now = datetime.now(timezone.utc)
        manager._instances["task-2"] = PreviewInstance(
            id="preview-task-2",
            task_id="task-2",
            work_dir=tmp_path,
            port=5174,
            status="running",
            started_at=now,
            last_accessed_at=now,
        )
        manager._used_ports.add(5174)
        monkeypatch.setattr(preview_service, "is_node_available", lambda: False)

        started = await manager.startPreview({"taskId": "task-2", "workDir": str(tmp_path), "port": 5174})
        assert started["status"] == "running"
        status = manager.getStatus("task-2")
        assert status["status"] == "running"
        stopped = await manager.stopPreview("task-2")
        assert stopped["status"] == "stopped"

    asyncio.run(_run())
