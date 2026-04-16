from __future__ import annotations

import json
from pathlib import Path

from forgepilot_sdk.session import (
    append_to_session,
    delete_session,
    fork_session,
    get_session_info,
    get_session_messages,
    list_sessions,
    load_session,
    rename_session,
    save_session,
    tag_session,
)
from forgepilot_sdk.types import ConversationMessage


def test_save_load_list_and_get_messages(tmp_path) -> None:
    session_dir = tmp_path / "sessions"
    messages = [
        ConversationMessage(role="user", content="hello"),
        ConversationMessage(role="assistant", content="world"),
    ]

    save_session(
        "s1",
        messages,
        {"cwd": str(tmp_path), "model": "gpt-4.1", "summary": "My Session"},
        sessions_dir=session_dir,
    )

    transcript = session_dir / "s1" / "transcript.json"
    assert transcript.exists()
    raw = json.loads(transcript.read_text(encoding="utf-8"))
    assert raw["metadata"]["id"] == "s1"
    assert raw["metadata"]["messageCount"] == 2
    assert raw["metadata"]["summary"] == "My Session"
    assert len(raw["messages"]) == 2

    loaded = load_session("s1", sessions_dir=session_dir)
    assert loaded is not None
    assert loaded["metadata"]["id"] == "s1"
    assert loaded["metadata"]["model"] == "gpt-4.1"
    assert get_session_messages("s1", sessions_dir=session_dir)[0]["content"] == "hello"

    sessions = list_sessions(sessions_dir=session_dir)
    assert len(sessions) == 1
    assert sessions[0]["id"] == "s1"


def test_append_rename_tag_fork_delete(tmp_path) -> None:
    session_dir = tmp_path / "sessions"
    save_session("alpha", [ConversationMessage(role="user", content="start")], sessions_dir=session_dir)

    append_to_session("alpha", ConversationMessage(role="assistant", content="ok"), sessions_dir=session_dir)
    info = get_session_info("alpha", options={"dir": session_dir})
    assert info is not None
    assert info["messageCount"] == 2

    rename_session("alpha", "Renamed Session", options={"dir": session_dir})
    tag_session("alpha", "important", options={"dir": session_dir})
    info2 = get_session_info("alpha", options={"dir": session_dir})
    assert info2 is not None
    assert info2["summary"] == "Renamed Session"
    assert info2["tag"] == "important"

    forked = fork_session("alpha", sessions_dir=session_dir)
    assert forked is not None
    forked_data = load_session(forked, sessions_dir=session_dir)
    assert forked_data is not None
    assert len(forked_data["messages"]) == 2
    assert "Forked from session alpha" in str(forked_data["metadata"].get("summary"))

    assert delete_session("alpha", sessions_dir=session_dir) is True
    assert load_session("alpha", sessions_dir=session_dir) is None


def test_load_legacy_forgepilot_session_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("FORGEPILOT_SESSION_STRICT_PARITY", "1")

    legacy_root = Path(tmp_path) / ".forgepilot" / "sessions"
    legacy_root.mkdir(parents=True, exist_ok=True)
    legacy_file = legacy_root / "legacy-1.json"
    legacy_payload = {
        "session_id": "legacy-1",
        "messages": [{"role": "user", "content": "legacy"}],
        "metadata": {"summary": "legacy summary", "model": "claude"},
    }
    legacy_file.write_text(json.dumps(legacy_payload), encoding="utf-8")

    # Strict parity default follows upstream behavior: no legacy migration.
    loaded = load_session("legacy-1")
    assert loaded is None

    migrated = Path(tmp_path) / ".open-agent-sdk" / "sessions" / "legacy-1" / "transcript.json"
    assert not migrated.exists()


def test_load_legacy_migration_available_when_strict_parity_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("FORGEPILOT_SESSION_STRICT_PARITY", "0")

    legacy_root = Path(tmp_path) / ".forgepilot" / "sessions"
    legacy_root.mkdir(parents=True, exist_ok=True)
    legacy_file = legacy_root / "legacy-2.json"
    legacy_payload = {
        "session_id": "legacy-2",
        "messages": [{"role": "user", "content": "legacy"}],
        "metadata": {"summary": "legacy summary", "model": "claude"},
    }
    legacy_file.write_text(json.dumps(legacy_payload), encoding="utf-8")

    loaded = load_session("legacy-2")
    assert loaded is not None
    assert loaded["metadata"]["id"] == "legacy-2"
    assert loaded["metadata"]["summary"] == "legacy summary"
    assert loaded["messages"][0]["content"] == "legacy"

    migrated = Path(tmp_path) / ".open-agent-sdk" / "sessions" / "legacy-2" / "transcript.json"
    assert migrated.exists()


def test_append_creates_session_when_missing(tmp_path) -> None:
    session_dir = tmp_path / "sessions"
    append_to_session("new-one", ConversationMessage(role="user", content="hello"), sessions_dir=session_dir)

    # Parity with upstream TypeScript SDK:
    # append on missing session is a no-op.
    loaded = load_session("new-one", sessions_dir=session_dir)
    assert loaded is None


def test_load_session_returns_raw_malformed_payload_in_strict_parity(tmp_path) -> None:
    session_dir = tmp_path / "sessions"
    broken = session_dir / "bad-1" / "transcript.json"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text(json.dumps({"metadata": "oops", "messages": {"x": 1}}), encoding="utf-8")

    loaded = load_session("bad-1", sessions_dir=session_dir)
    assert loaded is not None
    assert loaded["metadata"] == "oops"
    assert loaded["messages"] == {"x": 1}


def test_default_model_matches_upstream_literal(tmp_path) -> None:
    session_dir = tmp_path / "sessions"

    save_session("m1", [ConversationMessage(role="user", content="hi")], sessions_dir=session_dir)
    loaded = load_session("m1", sessions_dir=session_dir)
    assert loaded is not None
    assert loaded["metadata"]["model"] == "claude-sonnet-4-6"

    listed = list_sessions(sessions_dir=session_dir)
    assert listed
    assert listed[0]["model"] == "claude-sonnet-4-6"


def test_delete_session_returns_true_for_missing_session(tmp_path) -> None:
    session_dir = tmp_path / "sessions"
    assert delete_session("absent", sessions_dir=session_dir) is True


