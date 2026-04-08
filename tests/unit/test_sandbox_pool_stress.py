from __future__ import annotations

import asyncio

from forgepilot_api.sandbox.pool import SandboxPool
from forgepilot_api.sandbox.types import SandboxCapabilities, SandboxExecResult


class StressProvider:
    type = "stress"
    name = "Stress"

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.stop_calls = 0

    async def is_available(self) -> bool:
        return True

    async def init(self, config: dict | None = None) -> None:
        del config

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


def test_pool_concurrent_acquire_release_consistency() -> None:
    async def _run() -> None:
        created: list[StressProvider] = []

        def _factory(_config=None):
            del _config
            provider = StressProvider(tag=f"p-{len(created) + 1}")
            created.append(provider)
            return provider

        pool = SandboxPool(_factory, max_size=4)

        async def _worker(worker_index: int) -> None:
            for _ in range(8):
                image = f"img-{worker_index % 3}"
                instance = await pool.acquire(image)
                await asyncio.sleep(0)
                pool.release(instance)

        await asyncio.gather(*[_worker(i) for i in range(24)])

        stats = pool.get_stats()
        assert stats.in_use == 0
        assert stats.total == stats.available
        assert stats.total >= 1

        await pool.stop_all()
        assert pool.get_stats().total == 0

    asyncio.run(_run())


def test_pool_stop_all_during_reacquire_cycle() -> None:
    async def _run() -> None:
        created: list[StressProvider] = []

        def _factory(_config=None):
            del _config
            provider = StressProvider(tag=f"p-{len(created) + 1}")
            created.append(provider)
            return provider

        pool = SandboxPool(_factory, max_size=2)

        first = await pool.acquire("img-a")
        pool.release(first)

        await pool.stop_all()
        assert pool.get_stats().total == 0

        second = await pool.acquire("img-a")
        pool.release(second)

        assert second.id != first.id
        await pool.stop_all()

    asyncio.run(_run())

