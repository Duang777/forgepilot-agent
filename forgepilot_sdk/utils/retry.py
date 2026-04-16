from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class RetryConfig:
    max_retries: int = 3
    base_delay_ms: int = 2000
    max_delay_ms: int = 30000
    retryable_status_codes: tuple[int, ...] = (429, 500, 502, 503, 529)


DEFAULT_RETRY_CONFIG = RetryConfig()


def is_retryable_error(err: Any, config: RetryConfig = DEFAULT_RETRY_CONFIG) -> bool:
    status = getattr(err, "status", None)
    if status in config.retryable_status_codes:
        return True
    code = getattr(err, "code", None)
    if code in {"ECONNRESET", "ETIMEDOUT", "ECONNREFUSED"}:
        return True
    if isinstance(err, dict):
        nested = ((err.get("error") or {}) if isinstance(err.get("error"), dict) else {})
        if nested.get("type") == "overloaded_error":
            return True
    return False


def get_retry_delay(attempt: int, config: RetryConfig = DEFAULT_RETRY_CONFIG) -> float:
    delay = config.base_delay_ms * (2**attempt)
    jitter = delay * 0.25 * (random.random() * 2 - 1)
    return min(delay + jitter, config.max_delay_ms) / 1000.0


def _is_aborted(signal: Any) -> bool:
    if signal is None:
        return False
    if hasattr(signal, "aborted"):
        try:
            return bool(signal.aborted)
        except Exception:
            return False
    if hasattr(signal, "is_set"):
        try:
            return bool(signal.is_set())
        except Exception:
            return False
    return False


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    config: RetryConfig | None = None,
    abort_signal: Any | None = None,
) -> T:
    cfg = config or DEFAULT_RETRY_CONFIG
    last_error: Exception | None = None
    for attempt in range(cfg.max_retries + 1):
        if _is_aborted(abort_signal):
            raise RuntimeError("Aborted")
        try:
            return await fn()
        except Exception as exc:
            last_error = exc
            if not is_retryable_error(exc, cfg) or attempt == cfg.max_retries:
                raise
            await asyncio.sleep(get_retry_delay(attempt, cfg))
    if last_error is not None:
        raise last_error
    raise RuntimeError("Retry failed")


def is_prompt_too_long_error(err: Any) -> bool:
    status = getattr(err, "status", None)
    if status != 400:
        return False
    message = str(getattr(err, "message", "") or "")
    if not message and isinstance(err, dict):
        message = str(((err.get("error") or {}).get("error") or {}).get("message") or "")
    lower = message.lower()
    return "prompt is too long" in lower or "max_tokens" in lower or "context length" in lower


def is_auth_error(err: Any) -> bool:
    return getattr(err, "status", None) in {401, 403}


def is_rate_limit_error(err: Any) -> bool:
    return getattr(err, "status", None) == 429


def format_api_error(err: Any) -> str:
    if is_auth_error(err):
        return "Authentication failed. Check your CODEANY_API_KEY."
    if is_rate_limit_error(err):
        return "Rate limit exceeded. Please retry after a short wait."
    if getattr(err, "status", None) == 529:
        return "API overloaded. Please retry later."
    if is_prompt_too_long_error(err):
        return "Prompt too long. Auto-compacting conversation..."
    return f"API error: {getattr(err, 'message', str(err))}"


def isRetryableError(err: Any, config: RetryConfig = DEFAULT_RETRY_CONFIG) -> bool:
    return is_retryable_error(err, config)


def getRetryDelay(attempt: int, config: RetryConfig = DEFAULT_RETRY_CONFIG) -> float:
    return get_retry_delay(attempt, config)


async def withRetry(
    fn: Callable[[], Awaitable[T]],
    config: RetryConfig | None = None,
    abortSignal: Any | None = None,
) -> T:
    return await with_retry(fn, config=config, abort_signal=abortSignal)


def isPromptTooLongError(err: Any) -> bool:
    return is_prompt_too_long_error(err)


def isAuthError(err: Any) -> bool:
    return is_auth_error(err)


def isRateLimitError(err: Any) -> bool:
    return is_rate_limit_error(err)


def formatApiError(err: Any) -> str:
    return format_api_error(err)
