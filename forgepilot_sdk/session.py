from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forgepilot_sdk.types import ConversationMessage

logger = logging.getLogger(__name__)
_DEFAULT_MODEL_FALLBACK = "claude-sonnet-4-6"
_LOCK_ACQUIRE_TIMEOUT_SECONDS = float(os.getenv("FORGEPILOT_SESSION_LOCK_TIMEOUT_SECONDS", "10"))
_LOCK_STALE_SECONDS = float(os.getenv("FORGEPILOT_SESSION_LOCK_STALE_SECONDS", "60"))
_LOCK_RETRY_INTERVAL_SECONDS = 0.05
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _strict_session_parity() -> bool:
    raw = os.getenv("FORGEPILOT_SESSION_STRICT_PARITY", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _default_model() -> str:
    if _strict_session_parity():
        return _DEFAULT_MODEL_FALLBACK
    model = os.getenv("FORGEPILOT_DEFAULT_MODEL", "").strip()
    return model or _DEFAULT_MODEL_FALLBACK


def _session_root(sessions_dir: str | Path | None = None) -> Path:
    if sessions_dir is not None:
        root = Path(sessions_dir).expanduser()
    else:
        root = Path.home() / ".open-agent-sdk" / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _session_path(session_id: str, sessions_dir: str | Path | None = None) -> Path:
    return _session_root(sessions_dir) / session_id


def _session_file(session_id: str, sessions_dir: str | Path | None = None) -> Path:
    return _session_path(session_id, sessions_dir) / "transcript.json"


def _legacy_session_file(session_id: str) -> Path:
    return Path.home() / ".forgepilot" / "sessions" / f"{session_id}.json"


def _normalize_message(message: ConversationMessage | dict[str, Any]) -> dict[str, Any]:
    if isinstance(message, ConversationMessage):
        return {"role": message.role, "content": message.content}
    if isinstance(message, dict):
        role = str(message.get("role") or "user")
        return {"role": role, "content": message.get("content")}
    return {"role": "user", "content": str(message)}


def _default_metadata(session_id: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = metadata or {}
    created_at = str(payload.get("createdAt") or _utc_now_iso())
    updated_at = str(payload.get("updatedAt") or _utc_now_iso())
    base: dict[str, Any] = {
        "id": session_id,
        "cwd": str(payload.get("cwd") or Path.cwd()),
        "model": str(payload.get("model") or _default_model()),
        "createdAt": created_at,
        "updatedAt": updated_at,
        "messageCount": 0,
    }
    if "summary" in payload:
        base["summary"] = payload.get("summary")
    if "tag" in payload:
        base["tag"] = payload.get("tag")
    return base


def _ensure_session_payload(
    session_id: str,
    messages: list[ConversationMessage | dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_messages = [_normalize_message(m) for m in messages]
    merged_metadata = _default_metadata(session_id, metadata)
    merged_metadata["messageCount"] = len(normalized_messages)
    return {"metadata": merged_metadata, "messages": normalized_messages}


def _normalize_loaded_payload(session_id: str, raw: Any) -> tuple[dict[str, Any], bool]:
    if not isinstance(raw, dict):
        return _ensure_session_payload(session_id, [], metadata={}), True

    raw_metadata = raw.get("metadata")
    raw_messages = raw.get("messages")
    repaired = not isinstance(raw_metadata, dict) or not isinstance(raw_messages, list)

    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    messages = raw_messages if isinstance(raw_messages, list) else []
    normalized = _ensure_session_payload(session_id, messages, metadata=metadata)

    # Preserve non-standard metadata fields (for compatibility) while enforcing invariants.
    merged_meta = {**normalized["metadata"], **metadata}
    if str(merged_meta.get("id") or "") != session_id:
        repaired = True
    merged_meta["id"] = session_id
    if merged_meta.get("messageCount") != len(normalized["messages"]):
        repaired = True
    merged_meta["messageCount"] = len(normalized["messages"])
    if not merged_meta.get("createdAt"):
        repaired = True
        merged_meta["createdAt"] = _utc_now_iso()
    if not merged_meta.get("updatedAt"):
        repaired = True
        merged_meta["updatedAt"] = str(merged_meta["createdAt"])
    if not merged_meta.get("cwd"):
        repaired = True
        merged_meta["cwd"] = str(Path.cwd())
    if not merged_meta.get("model"):
        repaired = True
        merged_meta["model"] = _default_model()

    normalized["metadata"] = merged_meta
    return normalized, repaired


def _session_key(session_id: str, sessions_dir: str | Path | None = None) -> str:
    try:
        return str(_session_path(session_id, sessions_dir=sessions_dir).resolve())
    except Exception:
        return str(_session_path(session_id, sessions_dir=sessions_dir))


def _get_thread_lock(key: str) -> threading.RLock:
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _THREAD_LOCKS[key] = lock
        return lock


@contextlib.contextmanager
def _session_write_lock(session_id: str, sessions_dir: str | Path | None = None):
    key = _session_key(session_id, sessions_dir=sessions_dir)
    thread_lock = _get_thread_lock(key)
    with thread_lock:
        lock_dir = _session_path(session_id, sessions_dir=sessions_dir)
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / ".transcript.lock"

        start = time.monotonic()
        fd: int | None = None

        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()} {time.time()}".encode("utf-8", errors="ignore"))
                break
            except FileExistsError:
                try:
                    age = time.time() - lock_path.stat().st_mtime
                    if age > _LOCK_STALE_SECONDS:
                        lock_path.unlink(missing_ok=True)
                        continue
                except Exception:
                    pass

                if (time.monotonic() - start) > _LOCK_ACQUIRE_TIMEOUT_SECONDS:
                    raise TimeoutError(f"Timed out waiting for session write lock: {lock_path}")
                time.sleep(_LOCK_RETRY_INTERVAL_SECONDS)

        try:
            yield
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except Exception:
                    pass
            try:
                lock_path.unlink(missing_ok=True)
            except Exception:
                pass


def _write_session_payload(
    session_id: str,
    payload: dict[str, Any],
    *,
    sessions_dir: str | Path | None = None,
) -> None:
    file_path = _session_file(session_id, sessions_dir=sessions_dir)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = file_path.with_name(f".{file_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, file_path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _load_session_unlocked(
    session_id: str,
    *,
    sessions_dir: str | Path | None = None,
    migrate_legacy: bool = True,
    persist_repaired: bool = True,
    lock_held: bool = False,
) -> dict[str, Any] | None:
    path = _session_file(session_id, sessions_dir=sessions_dir)
    if not path.exists():
        if not migrate_legacy:
            return None
        legacy = _load_legacy_session(session_id)
        if not legacy:
            return None
        if migrate_legacy:
            if lock_held:
                _write_session_payload(session_id, legacy, sessions_dir=sessions_dir)
            else:
                with _session_write_lock(session_id, sessions_dir=sessions_dir):
                    if not _session_file(session_id, sessions_dir=sessions_dir).exists():
                        _write_session_payload(session_id, legacy, sessions_dir=sessions_dir)
        return legacy

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("failed to parse session transcript session_id=%s path=%s error=%s", session_id, path, exc)
        return None

    if _strict_session_parity():
        return raw if isinstance(raw, dict) else None

    normalized, repaired = _normalize_loaded_payload(session_id, raw)
    if not repaired or not persist_repaired:
        return normalized

    if lock_held:
        _write_session_payload(session_id, normalized, sessions_dir=sessions_dir)
        return normalized

    with _session_write_lock(session_id, sessions_dir=sessions_dir):
        latest_path = _session_file(session_id, sessions_dir=sessions_dir)
        if latest_path.exists():
            try:
                latest_raw = json.loads(latest_path.read_text(encoding="utf-8"))
                latest_normalized, latest_repaired = _normalize_loaded_payload(session_id, latest_raw)
            except Exception:
                latest_normalized, latest_repaired = normalized, True
            if latest_repaired:
                _write_session_payload(session_id, latest_normalized, sessions_dir=sessions_dir)
            return latest_normalized

        _write_session_payload(session_id, normalized, sessions_dir=sessions_dir)
        return normalized


def save_session(
    session_id: str,
    messages: list[ConversationMessage | dict[str, Any]],
    metadata: dict[str, Any] | None = None,
    *,
    sessions_dir: str | Path | None = None,
) -> None:
    if _strict_session_parity():
        payload = _ensure_session_payload(session_id, messages, metadata or {})
        _write_session_payload(session_id, payload, sessions_dir=sessions_dir)
        return

    with _session_write_lock(session_id, sessions_dir=sessions_dir):
        existing = _load_session_unlocked(
            session_id,
            sessions_dir=sessions_dir,
            migrate_legacy=True,
            persist_repaired=False,
            lock_held=True,
        )
        existing_meta = existing.get("metadata") if existing else {}
        merged_meta = {**(existing_meta if isinstance(existing_meta, dict) else {}), **(metadata or {})}
        merged_meta["id"] = session_id
        merged_meta["createdAt"] = str(merged_meta.get("createdAt") or _utc_now_iso())
        merged_meta["updatedAt"] = _utc_now_iso()
        payload = _ensure_session_payload(session_id, messages, merged_meta)
        _write_session_payload(session_id, payload, sessions_dir=sessions_dir)


def append_to_session(
    session_id: str,
    message: ConversationMessage | dict[str, Any],
    *,
    sessions_dir: str | Path | None = None,
) -> None:
    with _session_write_lock(session_id, sessions_dir=sessions_dir):
        data = _load_session_unlocked(
            session_id,
            sessions_dir=sessions_dir,
            migrate_legacy=True,
            persist_repaired=False,
            lock_held=True,
        )
        if not data:
            if _strict_session_parity():
                return
            messages = [_normalize_message(message)]
            metadata: dict[str, Any] = {
                "id": session_id,
                "createdAt": _utc_now_iso(),
                "updatedAt": _utc_now_iso(),
            }
            payload = _ensure_session_payload(session_id, messages, metadata)
            _write_session_payload(session_id, payload, sessions_dir=sessions_dir)
            return

        messages = data.get("messages", [])
        if not isinstance(messages, list):
            messages = []
        messages = [*messages, _normalize_message(message)]

        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["updatedAt"] = _utc_now_iso()
        metadata["messageCount"] = len(messages)
        metadata["id"] = session_id

        payload = _ensure_session_payload(session_id, messages, metadata)
        _write_session_payload(session_id, payload, sessions_dir=sessions_dir)


def _load_legacy_session(session_id: str) -> dict[str, Any] | None:
    path = _legacy_session_file(session_id)
    if not path.exists():
        return None
    try:
        legacy = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    raw_messages = legacy.get("messages", [])
    if not isinstance(raw_messages, list):
        raw_messages = []
    metadata = legacy.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    if "summary" not in metadata and isinstance(legacy.get("summary"), str):
        metadata["summary"] = legacy["summary"]
    if "createdAt" not in metadata and isinstance(legacy.get("createdAt"), str):
        metadata["createdAt"] = legacy["createdAt"]

    return _ensure_session_payload(session_id, raw_messages, metadata)


def load_session(session_id: str, *, sessions_dir: str | Path | None = None) -> dict[str, Any] | None:
    return _load_session_unlocked(
        session_id,
        sessions_dir=sessions_dir,
        migrate_legacy=not _strict_session_parity(),
        persist_repaired=True,
        lock_held=False,
    )


def list_sessions(*, sessions_dir: str | Path | None = None) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    for path in _session_root(sessions_dir).glob("*/transcript.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if _strict_session_parity():
                if not isinstance(raw, dict):
                    continue
                metadata = raw.get("metadata")
                if isinstance(metadata, dict):
                    sessions.append(metadata)
                continue

            normalized, _ = _normalize_loaded_payload(path.parent.name, raw)
            sessions.append(normalized["metadata"])
        except Exception:
            continue
    return sorted(sessions, key=lambda x: str(x.get("updatedAt") or ""), reverse=True)


def fork_session(
    source_session_id: str,
    new_session_id: str | None = None,
    *,
    sessions_dir: str | Path | None = None,
) -> str | None:
    current = load_session(source_session_id, sessions_dir=sessions_dir)
    if not current:
        return None
    fork_id = new_session_id or str(uuid.uuid4())
    metadata = current.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    merged_metadata = {
        **metadata,
        "id": fork_id,
        "createdAt": _utc_now_iso(),
        "updatedAt": _utc_now_iso(),
        "summary": f"Forked from session {source_session_id}",
    }
    save_session(fork_id, current.get("messages", []), merged_metadata, sessions_dir=sessions_dir)
    return fork_id


def get_session_messages(session_id: str, *, sessions_dir: str | Path | None = None) -> list[dict[str, Any]]:
    data = load_session(session_id, sessions_dir=sessions_dir)
    if not data:
        return []
    messages = data.get("messages")
    return messages if isinstance(messages, list) else []


def get_session_info(
    session_id: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    sessions_dir = options.get("dir") if isinstance(options, dict) else None
    data = load_session(session_id, sessions_dir=sessions_dir)
    if not data:
        return None
    metadata = data.get("metadata")
    return metadata if isinstance(metadata, dict) else None


def rename_session(
    session_id: str,
    title: str,
    options: dict[str, Any] | None = None,
) -> None:
    sessions_dir = options.get("dir") if isinstance(options, dict) else None
    data = load_session(session_id, sessions_dir=sessions_dir)
    if not data:
        return
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["summary"] = title
    metadata["updatedAt"] = _utc_now_iso()
    save_session(session_id, data.get("messages", []), metadata, sessions_dir=sessions_dir)


def tag_session(
    session_id: str,
    tag: str | None,
    options: dict[str, Any] | None = None,
) -> None:
    sessions_dir = options.get("dir") if isinstance(options, dict) else None
    data = load_session(session_id, sessions_dir=sessions_dir)
    if not data:
        return
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["tag"] = tag
    metadata["updatedAt"] = _utc_now_iso()
    save_session(session_id, data.get("messages", []), metadata, sessions_dir=sessions_dir)


def delete_session(session_id: str, *, sessions_dir: str | Path | None = None) -> bool:
    try:
        path = _session_path(session_id, sessions_dir=sessions_dir)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        legacy = _legacy_session_file(session_id)
        legacy.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def saveSession(
    sessionId: str,
    messages: list[ConversationMessage | dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> None:
    save_session(sessionId, messages, metadata)


def loadSession(sessionId: str) -> dict[str, Any] | None:
    return load_session(sessionId)


def listSessions() -> list[dict[str, Any]]:
    return list_sessions()


def forkSession(sourceSessionId: str, newSessionId: str | None = None) -> str | None:
    return fork_session(sourceSessionId, newSessionId)


def getSessionMessages(sessionId: str) -> list[dict[str, Any]]:
    return get_session_messages(sessionId)


def appendToSession(sessionId: str, message: ConversationMessage | dict[str, Any]) -> None:
    append_to_session(sessionId, message)


def deleteSession(sessionId: str) -> bool:
    return delete_session(sessionId)


def getSessionInfo(sessionId: str, options: dict[str, Any] | None = None) -> dict[str, Any] | None:
    return get_session_info(sessionId, options)


def renameSession(sessionId: str, title: str, options: dict[str, Any] | None = None) -> None:
    rename_session(sessionId, title, options)


def tagSession(sessionId: str, tag: str | None, options: dict[str, Any] | None = None) -> None:
    tag_session(sessionId, tag, options)

