from __future__ import annotations

import asyncio

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


