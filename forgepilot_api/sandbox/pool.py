from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from forgepilot_api.sandbox.types import ISandboxProvider


@dataclass(slots=True)
class PooledSandboxConfig:
    image: str | None = None
    memory_mib: int | None = None
    cpus: int | None = None
    work_dir: str | None = None
    timeout: int | None = None
    env: dict[str, str] | None = None
    volumes: list[dict[str, Any]] | None = None


@dataclass(slots=True)
class PooledSandbox:
    id: str
    image: str
    created_at: datetime
    last_used_at: datetime
    in_use: bool
    provider: ISandboxProvider


@dataclass(slots=True)
class PoolStats:
    total: int = 0
    in_use: int = 0
    available: int = 0
    by_image: dict[str, int] = field(default_factory=dict)


ProviderFactory = Callable[[PooledSandboxConfig | None], ISandboxProvider]


class SandboxPool:
    def __init__(self, provider_factory: ProviderFactory, max_size: int = 5) -> None:
        self._provider_factory = provider_factory
        self._max_size = max(1, int(max_size))
        self._pool: dict[str, list[PooledSandbox]] = {}
        self._id_counter = 0
        self._lock = asyncio.Lock()

    async def acquire(self, image: str, config: PooledSandboxConfig | None = None) -> PooledSandbox:
        async with self._lock:
            instances = self._pool.get(image, [])
            for instance in instances:
                if not instance.in_use:
                    instance.in_use = True
                    instance.last_used_at = datetime.utcnow()
                    return instance

            await self._cleanup_if_needed()

            merged = config or PooledSandboxConfig()
            merged.image = image
            provider = self._provider_factory(merged)
            await provider.init(
                {
                    "image": image,
                    "memoryMib": merged.memory_mib or 1024,
                    "cpus": merged.cpus or 2,
                    "workDir": merged.work_dir or "/workspace",
                    "timeout": merged.timeout,
                    "env": merged.env,
                    "volumes": merged.volumes,
                }
            )

            self._id_counter += 1
            now = datetime.utcnow()
            instance = PooledSandbox(
                id=f"sandbox-{self._id_counter}",
                image=image,
                created_at=now,
                last_used_at=now,
                in_use=True,
                provider=provider,
            )
            self._pool.setdefault(image, []).append(instance)
            return instance

    def release(self, instance: PooledSandbox) -> None:
        instance.in_use = False
        instance.last_used_at = datetime.utcnow()

    async def _cleanup_if_needed(self) -> None:
        if self._total_count() < self._max_size:
            return

        oldest_image: str | None = None
        oldest_instance: PooledSandbox | None = None
        for image, instances in self._pool.items():
            for instance in instances:
                if instance.in_use:
                    continue
                if oldest_instance is None or instance.last_used_at < oldest_instance.last_used_at:
                    oldest_instance = instance
                    oldest_image = image

        if oldest_image and oldest_instance:
            await self._remove_instance(oldest_image, oldest_instance)

    async def _remove_instance(self, image: str, instance: PooledSandbox) -> None:
        try:
            await instance.provider.stop()
        except Exception:
            pass
        instances = self._pool.get(image, [])
        self._pool[image] = [item for item in instances if item is not instance]
        if not self._pool[image]:
            self._pool.pop(image, None)

    def _total_count(self) -> int:
        return sum(len(items) for items in self._pool.values())

    async def stop_all(self) -> None:
        async with self._lock:
            for instances in list(self._pool.values()):
                for instance in instances:
                    try:
                        await instance.provider.stop()
                    except Exception:
                        pass
            self._pool = {}

    def get_stats(self) -> PoolStats:
        stats = PoolStats()
        for image, instances in self._pool.items():
            stats.by_image[image] = len(instances)
            for instance in instances:
                stats.total += 1
                if instance.in_use:
                    stats.in_use += 1
                else:
                    stats.available += 1
        return stats

    def get_max_size(self) -> int:
        return self._max_size

    def set_max_size(self, size: int) -> None:
        self._max_size = max(1, int(size))


_global_pool: SandboxPool | None = None


def init_global_sandbox_pool(provider_factory: ProviderFactory, max_size: int = 5) -> SandboxPool:
    global _global_pool
    _global_pool = SandboxPool(provider_factory, max_size)
    return _global_pool


def get_global_sandbox_pool(
    provider_factory: ProviderFactory | None = None,
    max_size: int | None = None,
) -> SandboxPool:
    global _global_pool
    if _global_pool is None:
        if provider_factory is None:
            raise RuntimeError("Global sandbox pool not initialized. Call with provider_factory first.")
        _global_pool = SandboxPool(provider_factory, max_size or 5)
    return _global_pool


async def shutdown_global_sandbox_pool() -> None:
    global _global_pool
    if _global_pool is not None:
        await _global_pool.stop_all()
        _global_pool = None

