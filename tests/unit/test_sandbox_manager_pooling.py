from __future__ import annotations

import asyncio

import forgepilot_api.sandbox.manager as sandbox_manager_module
from forgepilot_api.sandbox.manager import (
    acquire_provider_with_fallback,
    get_sandbox_info,
    get_pool_stats,
    get_provider_with_fallback,
    stop_all_providers,
)


def test_acquire_provider_lease_with_pool_reuses_instance(monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.setenv("FORGEPILOT_SANDBOX_POOL_ENABLED", "1")
        monkeypatch.setenv("FORGEPILOT_SANDBOX_POOL_MAX_SIZE", "2")

        await stop_all_providers()

        lease1 = await acquire_provider_with_fallback("native", image="python:3.11-slim")
        provider1 = lease1.provider
        lease1.release()

        lease2 = await acquire_provider_with_fallback("native", image="python:3.11-slim")
        provider2 = lease2.provider
        lease2.release()

        assert provider1 is provider2

        await stop_all_providers()

    asyncio.run(_run())


def test_acquire_provider_lease_reports_fallback(monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.setenv("FORGEPILOT_SANDBOX_POOL_ENABLED", "0")

        lease = await acquire_provider_with_fallback("not-exists")

        assert lease.used_fallback is True
        assert lease.fallback_reason is not None
        assert lease.provider.type in {"native", "codex", "claude"}

        lease.release()

    asyncio.run(_run())


def test_non_lease_provider_getter_does_not_touch_pool(monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.setenv("FORGEPILOT_SANDBOX_POOL_ENABLED", "1")
        await stop_all_providers()

        provider, used_fallback, reason = await get_provider_with_fallback("native")
        assert provider.type == "native"
        assert used_fallback is False
        assert reason is None

        stats = get_pool_stats()
        assert stats == {}
        await stop_all_providers()

    asyncio.run(_run())


def test_get_sandbox_info_does_not_occupy_pool(monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.setenv("FORGEPILOT_SANDBOX_POOL_ENABLED", "1")
        await stop_all_providers()

        info = await get_sandbox_info()
        assert info["available"] in {True, False}

        # get_sandbox_info uses non-lease path and should not allocate pool entries
        assert get_pool_stats() == {}
        await stop_all_providers()

    asyncio.run(_run())


class _FakeProvider:
    def __init__(self, provider_type: str, available: bool) -> None:
        self.type = provider_type
        self.name = provider_type
        self._available = available

    async def is_available(self) -> bool:
        return self._available


class _FakeRegistry:
    def __init__(self, providers: dict[str, _FakeProvider]) -> None:
        self.providers = providers

    async def get_instance(self, provider_type: str):
        return self.providers[provider_type]


def test_production_blocks_implicit_native_fallback(monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.setenv("FORGEPILOT_SANDBOX_POOL_ENABLED", "0")
        monkeypatch.setenv("NODE_ENV", "production")
        monkeypatch.setenv("FORGEPILOT_SANDBOX_ALLOW_NATIVE_FALLBACK", "0")

        fake_registry = _FakeRegistry(
            {
                "codex": _FakeProvider("codex", available=False),
                "claude": _FakeProvider("claude", available=False),
                "native": _FakeProvider("native", available=True),
            }
        )
        monkeypatch.setattr(sandbox_manager_module, "get_sandbox_registry", lambda: fake_registry)

        try:
            await acquire_provider_with_fallback("codex")
            assert False, "expected native fallback policy rejection in production"
        except RuntimeError as exc:
            assert "native fallback is disabled by policy" in str(exc).lower()

    asyncio.run(_run())


def test_production_allows_explicit_native_provider(monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.setenv("FORGEPILOT_SANDBOX_POOL_ENABLED", "0")
        monkeypatch.setenv("NODE_ENV", "production")
        monkeypatch.setenv("FORGEPILOT_SANDBOX_ALLOW_NATIVE_FALLBACK", "0")

        fake_registry = _FakeRegistry(
            {
                "codex": _FakeProvider("codex", available=False),
                "claude": _FakeProvider("claude", available=False),
                "native": _FakeProvider("native", available=True),
            }
        )
        monkeypatch.setattr(sandbox_manager_module, "get_sandbox_registry", lambda: fake_registry)

        lease = await acquire_provider_with_fallback("native")
        assert lease.provider.type == "native"
        assert lease.used_fallback is False
        lease.release()

    asyncio.run(_run())

