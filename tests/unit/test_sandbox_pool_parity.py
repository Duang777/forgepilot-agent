from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from forgepilot_api.sandbox.pool import (
    SandboxPool,
    get_global_sandbox_pool,
    init_global_sandbox_pool,
    shutdown_global_sandbox_pool,
)
from forgepilot_api.sandbox.types import SandboxCapabilities, SandboxExecResult


class DummyProvider:
    type = "dummy"
    name = "Dummy"

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.init_calls: list[dict] = []
        self.stop_calls = 0

    async def is_available(self) -> bool:
        return True

    async def init(self, config: dict | None = None) -> None:
        self.init_calls.append(config or {})

    async def exec(self, options):
        del options
        return SandboxExecResult(stdout="", stderr="", exit_code=0, duration=1)

    async def run_script(self, file_path: str, work_dir: str, options=None):
        del file_path, work_dir, options
        return SandboxExecResult(stdout="", stderr="", exit_code=0, duration=1)

    async def stop(self) -> None:
        self.stop_calls += 1

    async def shutdown(self) -> None:
        await self.stop()

    def get_capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            supports_volume_mounts=False,
            supports_networking=True,
            isolation="process",
            supported_runtimes=["python"],
            supports_pooling=False,
        )

    def set_volumes(self, volumes) -> None:
        del volumes


def test_pool_reuse_instance_after_release() -> None:
    async def _run() -> None:
        created: list[DummyProvider] = []

        def _factory(_config=None):
            del _config
            provider = DummyProvider(tag=f"p-{len(created) + 1}")
            created.append(provider)
            return provider

        pool = SandboxPool(_factory, max_size=2)

        first = await pool.acquire("node:18")
        pool.release(first)
        second = await pool.acquire("node:18")

        assert first is second
        assert second.provider is created[0]
        assert len(created) == 1

    asyncio.run(_run())


def test_pool_cleanup_oldest_unused_when_full() -> None:
    async def _run() -> None:
        created: list[DummyProvider] = []

        def _factory(_config=None):
            del _config
            provider = DummyProvider(tag=f"p-{len(created) + 1}")
            created.append(provider)
            return provider

        pool = SandboxPool(_factory, max_size=2)

        oldest = await pool.acquire("img-1")
        pool.release(oldest)
        oldest.last_used_at = datetime.now(timezone.utc) - timedelta(minutes=5)

        newer = await pool.acquire("img-2")
        pool.release(newer)

        third = await pool.acquire("img-3")

        stats = pool.get_stats()
        assert stats.total == 2
        assert "img-1" not in stats.by_image
        assert "img-3" in stats.by_image
        assert created[0].stop_calls == 1
        assert third.provider is created[2]

    asyncio.run(_run())


def test_pool_stop_all_clears_instances() -> None:
    async def _run() -> None:
        created: list[DummyProvider] = []

        def _factory(_config=None):
            del _config
            provider = DummyProvider(tag=f"p-{len(created) + 1}")
            created.append(provider)
            return provider

        pool = SandboxPool(_factory, max_size=3)

        a = await pool.acquire("img-a")
        b = await pool.acquire("img-b")
        pool.release(a)
        pool.release(b)

        await pool.stop_all()

        stats = pool.get_stats()
        assert stats.total == 0
        assert all(item.stop_calls == 1 for item in created)

    asyncio.run(_run())


def test_global_pool_lifecycle() -> None:
    async def _run() -> None:
        created: list[DummyProvider] = []

        def _factory(_config=None):
            del _config
            provider = DummyProvider(tag=f"p-{len(created) + 1}")
            created.append(provider)
            return provider

        pool = init_global_sandbox_pool(_factory, max_size=1)
        assert get_global_sandbox_pool() is pool

        instance = await pool.acquire("img")
        pool.release(instance)

        await shutdown_global_sandbox_pool()

        with pytest.raises(RuntimeError):
            get_global_sandbox_pool()

    asyncio.run(_run())
