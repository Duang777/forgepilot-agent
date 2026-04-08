from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forgepilot_sdk.types import ConversationMessage


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
        "model": str(payload.get("model") or "claude-sonnet-4-6"),
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


def _write_session_payload(
    session_id: str,
    payload: dict[str, Any],
    *,
    sessions_dir: str | Path | None = None,
) -> None:
    file_path = _session_file(session_id, sessions_dir=sessions_dir)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def save_session(
    session_id: str,
    messages: list[ConversationMessage | dict[str, Any]],
    metadata: dict[str, Any] | None = None,
    *,
    sessions_dir: str | Path | None = None,
) -> None:
    existing = load_session(session_id, sessions_dir=sessions_dir)
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
    data = load_session(session_id, sessions_dir=sessions_dir)
    if not data:
        return

    messages = data.get("messages", [])
    if not isinstance(messages, list):
        messages = []
    messages.append(_normalize_message(message))

    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["updatedAt"] = _utc_now_iso()
    metadata["messageCount"] = len(messages)
    metadata["id"] = session_id

    save_session(session_id, messages, metadata, sessions_dir=sessions_dir)


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
    path = _session_file(session_id, sessions_dir=sessions_dir)
    if not path.exists():
        legacy = _load_legacy_session(session_id)
        if legacy:
            _write_session_payload(session_id, legacy, sessions_dir=sessions_dir)
            return legacy
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        metadata = data.get("metadata")
        messages = data.get("messages")
        if not isinstance(metadata, dict) or not isinstance(messages, list):
            return _ensure_session_payload(session_id, messages if isinstance(messages, list) else [], metadata={})
        normalized = _ensure_session_payload(session_id, messages, metadata=metadata)
        # Preserve non-standard metadata fields (for compatibility).
        merged_meta = {**normalized["metadata"], **metadata}
        normalized["metadata"] = merged_meta
        normalized["metadata"]["messageCount"] = len(normalized["messages"])
        normalized["metadata"]["id"] = session_id
        return normalized
    except Exception:
        return None


def list_sessions(*, sessions_dir: str | Path | None = None) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    for path in _session_root(sessions_dir).glob("*/transcript.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            metadata = data.get("metadata")
            messages = data.get("messages")
            if not isinstance(metadata, dict) or not isinstance(messages, list):
                continue
            metadata = {**metadata}
            metadata["id"] = str(metadata.get("id") or path.parent.name)
            metadata["messageCount"] = len(messages)
            metadata["updatedAt"] = str(metadata.get("updatedAt") or metadata.get("createdAt") or _utc_now_iso())
            metadata["createdAt"] = str(metadata.get("createdAt") or metadata["updatedAt"])
            metadata["cwd"] = str(metadata.get("cwd") or Path.cwd())
            metadata["model"] = str(metadata.get("model") or "claude-sonnet-4-6")
            sessions.append(metadata)
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
    removed = False
    path = _session_path(session_id, sessions_dir=sessions_dir)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
        removed = True
    legacy = _legacy_session_file(session_id)
    if legacy.exists():
        legacy.unlink(missing_ok=True)
        removed = True
    return removed

