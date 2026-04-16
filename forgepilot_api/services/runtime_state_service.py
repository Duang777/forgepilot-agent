from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from forgepilot_api.core.logging import get_logger
from forgepilot_api.core.settings import get_settings
from forgepilot_api.storage import repositories as sqlite_repo

logger = get_logger(__name__)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expires_at_iso(ttl_seconds: int | None) -> str | None:
    if ttl_seconds is None:
        return None
    ttl = max(1, int(ttl_seconds))
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()


def _normalize_ttl_seconds(ttl_seconds: int | None) -> int | None:
    if ttl_seconds is None:
        return None
    return max(1, int(ttl_seconds))


class RuntimeStateBackend(Protocol):
    async def close(self) -> None: ...
    async def create_runtime_session(self, session_id: str, phase: str) -> dict[str, Any]: ...
    async def get_runtime_session(self, session_id: str) -> dict[str, Any] | None: ...
    async def set_runtime_session_aborted(self, session_id: str, aborted: bool = True) -> bool: ...
    async def delete_runtime_session(self, session_id: str) -> bool: ...
    async def save_runtime_plan(self, plan: dict[str, Any], ttl_seconds: int | None = None) -> dict[str, Any]: ...
    async def get_runtime_plan(self, plan_id: str) -> dict[str, Any] | None: ...
    async def delete_runtime_plan(self, plan_id: str) -> bool: ...
    async def delete_expired_runtime_plans(self) -> int: ...
    async def register_runtime_permission(
        self,
        *,
        session_id: str,
        permission_id: str,
        payload: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]: ...
    async def get_runtime_permission(self, session_id: str, permission_id: str) -> dict[str, Any] | None: ...
    async def set_runtime_permission_status(self, session_id: str, permission_id: str, status: str) -> bool: ...
    async def delete_runtime_permission(self, session_id: str, permission_id: str) -> bool: ...
    async def delete_expired_runtime_permissions(self) -> int: ...
    async def wait_permission_event(
        self,
        *,
        session_id: str,
        permission_id: str,
        timeout_seconds: float,
    ) -> str | None: ...
    async def publish_permission_event(self, *, session_id: str, permission_id: str, status: str) -> None: ...


class SqliteRuntimeStateBackend:
    async def close(self) -> None:
        return None

    async def create_runtime_session(self, session_id: str, phase: str) -> dict[str, Any]:
        return await sqlite_repo.create_runtime_session(session_id, phase)

    async def get_runtime_session(self, session_id: str) -> dict[str, Any] | None:
        return await sqlite_repo.get_runtime_session(session_id)

    async def set_runtime_session_aborted(self, session_id: str, aborted: bool = True) -> bool:
        return await sqlite_repo.set_runtime_session_aborted(session_id, aborted)

    async def delete_runtime_session(self, session_id: str) -> bool:
        return await sqlite_repo.delete_runtime_session(session_id)

    async def save_runtime_plan(self, plan: dict[str, Any], ttl_seconds: int | None = None) -> dict[str, Any]:
        return await sqlite_repo.save_runtime_plan(plan, ttl_seconds=ttl_seconds)

    async def get_runtime_plan(self, plan_id: str) -> dict[str, Any] | None:
        return await sqlite_repo.get_runtime_plan(plan_id)

    async def delete_runtime_plan(self, plan_id: str) -> bool:
        return await sqlite_repo.delete_runtime_plan(plan_id)

    async def delete_expired_runtime_plans(self) -> int:
        return await sqlite_repo.delete_expired_runtime_plans()

    async def register_runtime_permission(
        self,
        *,
        session_id: str,
        permission_id: str,
        payload: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        return await sqlite_repo.register_runtime_permission(
            session_id=session_id,
            permission_id=permission_id,
            payload=payload,
            ttl_seconds=ttl_seconds,
        )

    async def get_runtime_permission(self, session_id: str, permission_id: str) -> dict[str, Any] | None:
        return await sqlite_repo.get_runtime_permission(session_id, permission_id)

    async def set_runtime_permission_status(self, session_id: str, permission_id: str, status: str) -> bool:
        return await sqlite_repo.set_runtime_permission_status(session_id, permission_id, status)

    async def delete_runtime_permission(self, session_id: str, permission_id: str) -> bool:
        return await sqlite_repo.delete_runtime_permission(session_id, permission_id)

    async def delete_expired_runtime_permissions(self) -> int:
        return await sqlite_repo.delete_expired_runtime_permissions()

    async def wait_permission_event(
        self,
        *,
        session_id: str,
        permission_id: str,
        timeout_seconds: float,
    ) -> str | None:
        del session_id, permission_id, timeout_seconds
        return None

    async def publish_permission_event(self, *, session_id: str, permission_id: str, status: str) -> None:
        del session_id, permission_id, status
        return None


class RedisRuntimeStateBackend:
    def __init__(self, client: Any, key_prefix: str) -> None:
        self._client = client
        self._prefix = (key_prefix or "forgepilot:runtime").rstrip(":")

    async def ping(self) -> None:
        await self._client.ping()

    async def close(self) -> None:
        close = getattr(self._client, "aclose", None)
        if callable(close):
            await close()
            return
        legacy_close = getattr(self._client, "close", None)
        if callable(legacy_close):
            maybe = legacy_close()
            if asyncio.iscoroutine(maybe):
                await maybe

    def _session_key(self, session_id: str) -> str:
        return f"{self._prefix}:session:{session_id}"

    def _plan_key(self, plan_id: str) -> str:
        return f"{self._prefix}:plan:{plan_id}"

    def _permission_key(self, session_id: str, permission_id: str) -> str:
        return f"{self._prefix}:permission:{session_id}:{permission_id}"

    def _permission_channel(self, session_id: str, permission_id: str) -> str:
        return f"{self._prefix}:events:permission:{session_id}:{permission_id}"

    async def _set_json(self, key: str, payload: dict[str, Any], ttl_seconds: int | None = None) -> None:
        text = json.dumps(payload, ensure_ascii=False)
        ttl = _normalize_ttl_seconds(ttl_seconds)
        if ttl is None:
            await self._client.set(key, text)
            return
        await self._client.set(key, text, ex=ttl)

    async def _get_json(self, key: str) -> dict[str, Any] | None:
        raw = await self._client.get(key)
        if raw is None:
            return None
        if not isinstance(raw, str):
            return None
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        if isinstance(parsed, dict):
            return parsed
        return None

    async def create_runtime_session(self, session_id: str, phase: str) -> dict[str, Any]:
        key = self._session_key(session_id)
        now = _now_utc_iso()
        existing = await self._get_json(key)
        created_at = str(existing.get("created_at") or now) if isinstance(existing, dict) else now
        payload = {
            "id": session_id,
            "phase": phase,
            "aborted": 0,
            "created_at": created_at,
            "updated_at": now,
        }
        await self._set_json(key, payload)
        return payload

    async def get_runtime_session(self, session_id: str) -> dict[str, Any] | None:
        return await self._get_json(self._session_key(session_id))

    async def set_runtime_session_aborted(self, session_id: str, aborted: bool = True) -> bool:
        key = self._session_key(session_id)
        existing = await self._get_json(key)
        if not existing:
            return False
        existing["aborted"] = 1 if aborted else 0
        existing["updated_at"] = _now_utc_iso()
        await self._set_json(key, existing)
        return True

    async def delete_runtime_session(self, session_id: str) -> bool:
        deleted = await self._client.delete(self._session_key(session_id))
        return int(deleted or 0) > 0

    async def save_runtime_plan(self, plan: dict[str, Any], ttl_seconds: int | None = None) -> dict[str, Any]:
        plan_id = str(plan.get("id") or "").strip()
        if not plan_id:
            raise ValueError("plan.id is required")

        now = _now_utc_iso()
        payload = {
            "id": plan_id,
            "payload": plan,
            "created_at": now,
            "expires_at": _expires_at_iso(ttl_seconds),
        }
        await self._set_json(self._plan_key(plan_id), payload, ttl_seconds=ttl_seconds)
        return payload

    async def get_runtime_plan(self, plan_id: str) -> dict[str, Any] | None:
        row = await self._get_json(self._plan_key(plan_id))
        if not row:
            return None
        payload = row.get("payload")
        if not isinstance(payload, dict):
            return None
        return row

    async def delete_runtime_plan(self, plan_id: str) -> bool:
        deleted = await self._client.delete(self._plan_key(plan_id))
        return int(deleted or 0) > 0

    async def delete_expired_runtime_plans(self) -> int:
        # Redis expiry handles this via key TTL.
        return 0

    async def register_runtime_permission(
        self,
        *,
        session_id: str,
        permission_id: str,
        payload: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        key = self._permission_key(session_id, permission_id)
        now = _now_utc_iso()
        existing = await self._get_json(key)
        created_at = str(existing.get("created_at") or now) if isinstance(existing, dict) else now
        row = {
            "session_id": session_id,
            "permission_id": permission_id,
            "payload": payload or {},
            "status": "pending",
            "created_at": created_at,
            "updated_at": now,
            "expires_at": _expires_at_iso(ttl_seconds),
        }
        await self._set_json(key, row, ttl_seconds=ttl_seconds)
        return row

    async def get_runtime_permission(self, session_id: str, permission_id: str) -> dict[str, Any] | None:
        row = await self._get_json(self._permission_key(session_id, permission_id))
        if not row:
            return None
        payload = row.get("payload")
        if payload is None:
            row["payload"] = {}
        return row

    async def set_runtime_permission_status(self, session_id: str, permission_id: str, status: str) -> bool:
        key = self._permission_key(session_id, permission_id)
        row = await self._get_json(key)
        if not row:
            return False
        row["status"] = status
        row["updated_at"] = _now_utc_iso()
        ttl = await self._client.ttl(key)
        if isinstance(ttl, int) and ttl > 0:
            row["expires_at"] = _expires_at_iso(ttl)
            await self._set_json(key, row, ttl_seconds=ttl)
            return True
        row["expires_at"] = None
        await self._set_json(key, row)
        return True

    async def delete_runtime_permission(self, session_id: str, permission_id: str) -> bool:
        deleted = await self._client.delete(self._permission_key(session_id, permission_id))
        return int(deleted or 0) > 0

    async def delete_expired_runtime_permissions(self) -> int:
        # Redis expiry handles this via key TTL.
        return 0

    async def wait_permission_event(
        self,
        *,
        session_id: str,
        permission_id: str,
        timeout_seconds: float,
    ) -> str | None:
        timeout = max(0.05, float(timeout_seconds))
        channel = self._permission_channel(session_id, permission_id)
        pubsub = self._client.pubsub(ignore_subscribe_messages=True)
        try:
            await pubsub.subscribe(channel)
            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return None
                wait_slice = min(1.0, remaining)
                message = await pubsub.get_message(timeout=wait_slice)
                if not message:
                    continue
                data = message.get("data")
                if isinstance(data, bytes):
                    text = data.decode("utf-8", errors="ignore").strip().lower()
                else:
                    text = str(data or "").strip().lower()
                if text:
                    return text
            return None
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(channel)
            with contextlib.suppress(Exception):
                close = getattr(pubsub, "aclose", None)
                if callable(close):
                    await close()
                else:
                    legacy = getattr(pubsub, "close", None)
                    if callable(legacy):
                        maybe = legacy()
                        if asyncio.iscoroutine(maybe):
                            await maybe

    async def publish_permission_event(self, *, session_id: str, permission_id: str, status: str) -> None:
        channel = self._permission_channel(session_id, permission_id)
        await self._client.publish(channel, status)


_BACKEND: RuntimeStateBackend | None = None
_BACKEND_SIGNATURE: tuple[str, str, str, bool] | None = None
_BACKEND_LOCK = asyncio.Lock()


async def _create_backend() -> RuntimeStateBackend:
    settings = get_settings()
    selected = settings.runtime_state_backend
    if selected != "redis":
        return SqliteRuntimeStateBackend()

    try:
        from redis.asyncio import Redis  # type: ignore
    except Exception as exc:
        if not settings.runtime_state_fail_open:
            raise RuntimeError("redis backend selected but redis package is not installed") from exc
        logger.warning("runtime-state redis package unavailable, fallback to sqlite backend")
        return SqliteRuntimeStateBackend()

    try:
        client = Redis.from_url(settings.runtime_state_redis_url, decode_responses=True)
        backend = RedisRuntimeStateBackend(client, key_prefix=settings.runtime_state_redis_key_prefix)
        await backend.ping()
        logger.info(
            "runtime-state backend initialized backend=redis prefix=%s",
            settings.runtime_state_redis_key_prefix,
        )
        return backend
    except Exception as exc:
        if not settings.runtime_state_fail_open:
            raise RuntimeError("failed to initialize runtime-state redis backend") from exc
        logger.warning("runtime-state redis init failed, fallback to sqlite backend err=%s", exc)
        return SqliteRuntimeStateBackend()


async def _get_backend() -> RuntimeStateBackend:
    global _BACKEND, _BACKEND_SIGNATURE

    settings = get_settings()
    signature = (
        settings.runtime_state_backend,
        settings.runtime_state_redis_url,
        settings.runtime_state_redis_key_prefix,
        settings.runtime_state_fail_open,
    )
    current = _BACKEND
    if current is not None and _BACKEND_SIGNATURE == signature:
        return current

    async with _BACKEND_LOCK:
        current = _BACKEND
        if current is not None and _BACKEND_SIGNATURE == signature:
            return current

        previous = _BACKEND
        backend = await _create_backend()
        _BACKEND = backend
        _BACKEND_SIGNATURE = signature
        if previous is not None and previous is not backend:
            try:
                await previous.close()
            except Exception:
                logger.debug("failed to close previous runtime-state backend", exc_info=True)
        return backend


async def reset_runtime_state_backend_cache() -> None:
    global _BACKEND, _BACKEND_SIGNATURE
    async with _BACKEND_LOCK:
        backend = _BACKEND
        _BACKEND = None
        _BACKEND_SIGNATURE = None
    if backend is not None:
        try:
            await backend.close()
        except Exception:
            logger.debug("failed to close runtime-state backend during reset", exc_info=True)


async def create_runtime_session(session_id: str, phase: str) -> dict[str, Any]:
    backend = await _get_backend()
    return await backend.create_runtime_session(session_id, phase)


async def get_runtime_session(session_id: str) -> dict[str, Any] | None:
    backend = await _get_backend()
    return await backend.get_runtime_session(session_id)


async def set_runtime_session_aborted(session_id: str, aborted: bool = True) -> bool:
    backend = await _get_backend()
    return await backend.set_runtime_session_aborted(session_id, aborted)


async def delete_runtime_session(session_id: str) -> bool:
    backend = await _get_backend()
    return await backend.delete_runtime_session(session_id)


async def save_runtime_plan(plan: dict[str, Any], ttl_seconds: int | None = None) -> dict[str, Any]:
    backend = await _get_backend()
    return await backend.save_runtime_plan(plan, ttl_seconds=ttl_seconds)


async def get_runtime_plan(plan_id: str) -> dict[str, Any] | None:
    backend = await _get_backend()
    return await backend.get_runtime_plan(plan_id)


async def delete_runtime_plan(plan_id: str) -> bool:
    backend = await _get_backend()
    return await backend.delete_runtime_plan(plan_id)


async def delete_expired_runtime_plans() -> int:
    backend = await _get_backend()
    return await backend.delete_expired_runtime_plans()


async def register_runtime_permission(
    *,
    session_id: str,
    permission_id: str,
    payload: dict[str, Any] | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    backend = await _get_backend()
    return await backend.register_runtime_permission(
        session_id=session_id,
        permission_id=permission_id,
        payload=payload,
        ttl_seconds=ttl_seconds,
    )


async def get_runtime_permission(session_id: str, permission_id: str) -> dict[str, Any] | None:
    backend = await _get_backend()
    return await backend.get_runtime_permission(session_id, permission_id)


async def set_runtime_permission_status(session_id: str, permission_id: str, status: str) -> bool:
    backend = await _get_backend()
    return await backend.set_runtime_permission_status(session_id, permission_id, status)


async def delete_runtime_permission(session_id: str, permission_id: str) -> bool:
    backend = await _get_backend()
    return await backend.delete_runtime_permission(session_id, permission_id)


async def delete_expired_runtime_permissions() -> int:
    backend = await _get_backend()
    return await backend.delete_expired_runtime_permissions()


async def wait_runtime_permission_event(
    *,
    session_id: str,
    permission_id: str,
    timeout_seconds: float,
) -> str | None:
    backend = await _get_backend()
    return await backend.wait_permission_event(
        session_id=session_id,
        permission_id=permission_id,
        timeout_seconds=timeout_seconds,
    )


async def publish_runtime_permission_event(
    *,
    session_id: str,
    permission_id: str,
    status: str,
) -> None:
    backend = await _get_backend()
    await backend.publish_permission_event(
        session_id=session_id,
        permission_id=permission_id,
        status=status,
    )
