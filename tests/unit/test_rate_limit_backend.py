from __future__ import annotations

import importlib

import pytest

from forgepilot_api.core.rate_limit import (
    InMemoryRateLimiter,
    RateLimiterUnavailable,
    build_rate_limiter,
)


def test_build_memory_backend() -> None:
    limiter = build_rate_limiter(
        backend="memory",
        redis_url="redis://127.0.0.1:6379/0",
        redis_key_prefix="forgepilot:test",
        fail_open=True,
    )
    assert isinstance(limiter, InMemoryRateLimiter)


def test_build_redis_backend_falls_back_when_package_missing(monkeypatch) -> None:
    real_import = importlib.import_module

    def _fake_import(name: str, package=None):
        if name == "redis.asyncio":
            raise ModuleNotFoundError("redis not installed")
        return real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", _fake_import)
    limiter = build_rate_limiter(
        backend="redis",
        redis_url="redis://127.0.0.1:6379/0",
        redis_key_prefix="forgepilot:test",
        fail_open=True,
    )
    assert isinstance(limiter, InMemoryRateLimiter)


def test_build_redis_backend_raises_when_package_missing_and_fail_closed(monkeypatch) -> None:
    real_import = importlib.import_module

    def _fake_import(name: str, package=None):
        if name == "redis.asyncio":
            raise ModuleNotFoundError("redis not installed")
        return real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", _fake_import)

    with pytest.raises(RateLimiterUnavailable):
        build_rate_limiter(
            backend="redis",
            redis_url="redis://127.0.0.1:6379/0",
            redis_key_prefix="forgepilot:test",
            fail_open=False,
        )
