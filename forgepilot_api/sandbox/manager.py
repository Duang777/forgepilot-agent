from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from forgepilot_api.sandbox.pool import PooledSandboxConfig, SandboxPool, shutdown_global_sandbox_pool
from forgepilot_api.sandbox.registry import get_sandbox_registry
from forgepilot_api.sandbox.types import ISandboxProvider, SandboxProviderType

SANDBOX_IMAGES = {
    "node": "node:18-alpine",
    "python": "python:3.11-slim",
    "bun": "oven/bun:latest",
}

_POOL_TRUTHY = {"1", "true", "yes", "on"}
_provider_pools: dict[str, SandboxPool] = {}


@dataclass(slots=True)
class ProviderLease:
    provider: ISandboxProvider
    used_fallback: bool
    fallback_reason: str | None
    release: Callable[[], None]


def _pool_enabled() -> bool:
    raw = os.getenv("FORGEPILOT_SANDBOX_POOL_ENABLED", "0").strip().lower()
    return raw in _POOL_TRUTHY


def _pool_max_size() -> int:
    raw = os.getenv("FORGEPILOT_SANDBOX_POOL_MAX_SIZE", "5").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 5


async def _select_provider_type_with_fallback(preferred_provider: SandboxProviderType | None = None) -> tuple[str, bool, str | None]:
    registry = get_sandbox_registry()

    if preferred_provider:
        preferred_key = str(preferred_provider)
        try:
            provider = await registry.get_instance(preferred_key)
            if await provider.is_available():
                return preferred_key, False, None
        except Exception:
            pass

    # Priority: codex -> claude -> native
    for name in ["codex", "claude", "native"]:
        provider = await registry.get_instance(name)
        if await provider.is_available():
            used_fallback = preferred_provider is not None and str(preferred_provider) != name
            reason = None
            if used_fallback:
                reason = f"Preferred provider '{preferred_provider}' unavailable, fallback to '{name}'."
            return name, used_fallback, reason

    raise RuntimeError("No sandbox provider available")


def _get_provider_pool(provider_type: str) -> SandboxPool:
    pool = _provider_pools.get(provider_type)
    if pool is None:
        registry = get_sandbox_registry()
        pool = SandboxPool(
            provider_factory=lambda _cfg=None, provider_key=provider_type, reg=registry: reg.create(provider_key),
            max_size=_pool_max_size(),
        )
        _provider_pools[provider_type] = pool
        return pool

    pool.set_max_size(_pool_max_size())
    return pool


def _build_pool_config(image: str, pool_config: dict | None = None) -> PooledSandboxConfig:
    raw = pool_config or {}
    return PooledSandboxConfig(
        image=image,
        memory_mib=int(raw.get("memoryMib")) if raw.get("memoryMib") is not None else None,
        cpus=int(raw.get("cpus")) if raw.get("cpus") is not None else None,
        work_dir=str(raw.get("workDir")) if raw.get("workDir") else None,
        timeout=int(raw.get("timeout")) if raw.get("timeout") is not None else None,
        env={str(k): str(v) for k, v in dict(raw.get("env") or {}).items()} or None,
        volumes=list(raw.get("volumes") or []) or None,
    )


async def acquire_provider_with_fallback(
    preferred_provider: SandboxProviderType | None = None,
    *,
    image: str | None = None,
    pool_config: dict | None = None,
) -> ProviderLease:
    provider_type, used_fallback, reason = await _select_provider_type_with_fallback(preferred_provider)
    registry = get_sandbox_registry()

    if not _pool_enabled():
        provider = await registry.get_instance(provider_type)
        return ProviderLease(
            provider=provider,
            used_fallback=used_fallback,
            fallback_reason=reason,
            release=lambda: None,
        )

    pool = _get_provider_pool(provider_type)
    resolved_image = image or SANDBOX_IMAGES["node"]
    pooled = await pool.acquire(resolved_image, _build_pool_config(resolved_image, pool_config))
    return ProviderLease(
        provider=pooled.provider,
        used_fallback=used_fallback,
        fallback_reason=reason,
        release=lambda: pool.release(pooled),
    )


async def get_provider_with_fallback(
    preferred_provider: SandboxProviderType | None = None,
) -> tuple[ISandboxProvider, bool, str | None]:
    # Legacy getter returns a provider without lease semantics.
    # Keep this non-pooled to avoid leaking pooled instances without release hooks.
    provider_type, used_fallback, reason = await _select_provider_type_with_fallback(preferred_provider)
    registry = get_sandbox_registry()
    provider = await registry.get_instance(provider_type)
    return provider, used_fallback, reason


def get_pool_stats() -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for provider_type, pool in _provider_pools.items():
        pool_stats = pool.get_stats()
        stats[provider_type] = {
            "total": pool_stats.total,
            "inUse": pool_stats.in_use,
            "available": pool_stats.available,
            "byImage": pool_stats.by_image,
            "maxSize": pool.get_max_size(),
        }
    return stats


async def get_sandbox_info() -> dict:
    try:
        provider, used_fallback, reason = await get_provider_with_fallback(None)
        caps = provider.get_capabilities()
        isolation_label = (
            "VM isolation"
            if caps.isolation == "vm"
            else "Container isolation"
            if caps.isolation == "container"
            else "Process isolation"
            if caps.isolation == "process"
            else "No isolation"
        )
        return {
            "available": True,
            "provider": provider.type,
            "providerName": provider.name,
            "isolation": caps.isolation,
            "mode": "vm" if caps.isolation == "vm" else "container" if caps.isolation == "container" else "fallback",
            "message": f"Using {provider.name} ({isolation_label})",
            "usedFallback": used_fallback,
            "fallbackReason": reason,
        }
    except Exception as exc:
        return {
            "available": False,
            "provider": "native",
            "providerName": "Native",
            "isolation": "none",
            "mode": "fallback",
            "message": "Sandbox is not available",
            "usedFallback": True,
            "fallbackReason": str(exc),
        }


async def stop_all_providers() -> None:
    registry = get_sandbox_registry()
    await registry.stop_all()
    for pool in list(_provider_pools.values()):
        await pool.stop_all()
    _provider_pools.clear()
    await shutdown_global_sandbox_pool()

