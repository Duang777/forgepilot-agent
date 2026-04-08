from __future__ import annotations

import asyncio
import importlib
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from forgepilot_api.core.logging import get_logger

logger = get_logger(__name__)

_REDIS_SCRIPT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local current = redis.call('INCR', key)
if current == 1 then
  redis.call('EXPIRE', key, window)
end
local ttl = redis.call('TTL', key)
if ttl < 0 then
  redis.call('EXPIRE', key, window)
  ttl = window
end
if current > limit then
  return {0, ttl}
end
return {1, ttl}
"""


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int | None = None


class RateLimiterUnavailable(RuntimeError):
    pass


class BaseRateLimiter:
    async def check(self, *, identity: str, max_requests: int, window_seconds: int) -> RateLimitResult:
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover
        return None


class InMemoryRateLimiter(BaseRateLimiter):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, deque[float]] = {}

    async def check(self, *, identity: str, max_requests: int, window_seconds: int) -> RateLimitResult:
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._buckets.get(identity)
            if bucket is None:
                bucket = deque()
                self._buckets[identity] = bucket
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= max_requests:
                retry_after = max(1, int(window_seconds - (now - bucket[0])))
                return RateLimitResult(allowed=False, retry_after_seconds=retry_after)
            bucket.append(now)
        return RateLimitResult(allowed=True)


class RedisRateLimiter(BaseRateLimiter):
    def __init__(
        self,
        *,
        redis_url: str,
        key_prefix: str,
        fail_open: bool,
        connect_timeout_seconds: float = 2.0,
    ) -> None:
        self.redis_url = redis_url
        self.key_prefix = key_prefix
        self.fail_open = fail_open
        self.connect_timeout_seconds = connect_timeout_seconds
        self._client: Any | None = None
        self._init_lock = asyncio.Lock()
        self._warned_runtime_error = False

    async def _get_client(self):
        if self._client is not None:
            return self._client
        async with self._init_lock:
            if self._client is not None:
                return self._client
            redis_mod = importlib.import_module("redis.asyncio")
            self._client = redis_mod.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=self.connect_timeout_seconds,
                socket_timeout=self.connect_timeout_seconds,
            )
            return self._client

    async def check(self, *, identity: str, max_requests: int, window_seconds: int) -> RateLimitResult:
        key = f"{self.key_prefix}:{window_seconds}:{identity}"
        try:
            client = await self._get_client()
            result = await client.eval(_REDIS_SCRIPT, 1, key, max_requests, window_seconds)
            allowed = bool(int(result[0]))
            retry_after = int(result[1]) if len(result) > 1 else None
            if allowed:
                return RateLimitResult(allowed=True)
            return RateLimitResult(allowed=False, retry_after_seconds=max(1, retry_after or 1))
        except Exception as exc:  # pragma: no cover - depends on external redis runtime
            if self.fail_open:
                if not self._warned_runtime_error:
                    logger.warning(
                        "redis rate limiter unavailable; fail-open enabled (%s). Falling back to allow.",
                        exc,
                    )
                    self._warned_runtime_error = True
                return RateLimitResult(allowed=True)
            raise RateLimiterUnavailable("Redis rate limiter unavailable") from exc

    async def close(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.aclose()
        except Exception:
            logger.exception("failed to close redis rate limiter client")
        finally:
            self._client = None


def build_rate_limiter(
    *,
    backend: str,
    redis_url: str,
    redis_key_prefix: str,
    fail_open: bool,
) -> BaseRateLimiter:
    normalized = (backend or "memory").strip().lower()
    if normalized not in {"memory", "redis"}:
        normalized = "memory"

    if normalized == "memory":
        return InMemoryRateLimiter()

    try:
        importlib.import_module("redis.asyncio")
    except Exception as exc:
        if fail_open:
            logger.warning("redis backend requested but unavailable (%s); using in-memory limiter", exc)
            return InMemoryRateLimiter()
        raise RateLimiterUnavailable("Redis backend requested but redis package is unavailable") from exc

    return RedisRateLimiter(
        redis_url=redis_url,
        key_prefix=redis_key_prefix,
        fail_open=fail_open,
    )
