from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from forgepilot_api.main import app


def _read_sse_payloads(response) -> list[dict]:
    payloads = []
    for raw in response.iter_lines():
        if not raw:
            continue
        line = raw.decode() if isinstance(raw, bytes) else raw
        if line.startswith("data: "):
            payloads.append(json.loads(line[6:]))
    return payloads


def test_health() -> None:
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_plan_stream_contract(monkeypatch) -> None:
    async def _fake_provider_config():
        return {"agent": {"config": {}}, "defaultModel": ""}

    monkeypatch.setattr("forgepilot_api.services.agent_service.load_codex_runtime_config", lambda: {})
    monkeypatch.setattr("forgepilot_api.services.agent_service.get_provider_config", _fake_provider_config)
    client = TestClient(app)
    with client.stream("POST", "/agent/plan", json={"prompt": "Create a Python script to parse CSV"}) as resp:
        assert resp.status_code == 200
        payloads = _read_sse_payloads(resp)

    event_types = [p.get("type") for p in payloads]
    assert "done" in event_types
    assert "error" in event_types
    errors = [p for p in payloads if p.get("type") == "error"]
    assert errors
    assert errors[0]["message"] == "__MODEL_NOT_CONFIGURED__"


def test_chat_stream_without_api_key_returns_error(monkeypatch) -> None:
    monkeypatch.setattr("forgepilot_api.services.chat_service.load_codex_runtime_config", lambda: {})
    client = TestClient(app)
    with client.stream("POST", "/agent/chat", json={"prompt": "hello"}) as resp:
        assert resp.status_code == 200
        payloads = _read_sse_payloads(resp)

    assert payloads[0]["type"] == "error"
    assert payloads[-1]["type"] == "done"


def test_health_dependencies_endpoints() -> None:
    client = TestClient(app)
    resp = client.get("/health/dependencies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["allRequiredInstalled"] is True

    resp2 = client.get("/health/dependencies/claude")
    assert resp2.status_code == 200
    assert resp2.json()["installed"] is True


def test_mcp_config_roundtrip(tmp_path) -> None:
    client = TestClient(app)
    payload = {"mcpServers": {"demo": {"type": "http", "url": "http://localhost:8080"}}}
    resp = client.post("/mcp/config", json=payload)
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    resp2 = client.get("/mcp/config")
    assert resp2.status_code == 200
    assert "mcpServers" in resp2.json()["data"]

    bad = client.post("/mcp/config", json={"invalid": True})
    assert bad.status_code == 400
    assert bad.json()["success"] is False


def test_providers_switch_and_config() -> None:
    client = TestClient(app)
    resp = client.post("/providers/sandbox/switch", json={"type": "native"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    resp2 = client.post("/providers/agents/switch", json={"type": "codeany"})
    assert resp2.status_code == 200
    assert resp2.json()["success"] is True

    resp3 = client.get("/providers/config")
    assert resp3.status_code == 200
    cfg = resp3.json()
    assert cfg["sandbox"]["type"] == "native"
    assert cfg["agent"]["type"] == "codeany"


def test_preview_and_sandbox_basic() -> None:
    client = TestClient(app)
    resp = client.get("/preview/node-available")
    assert resp.status_code == 200
    assert "available" in resp.json()

    start = client.post("/preview/start", json={"taskId": "t1", "workDir": "."})
    assert start.status_code == 200
    assert start.json()["status"] in {"running", "starting", "error"}

    status = client.get("/preview/status/t1")
    assert status.status_code == 200
    assert status.json()["status"] in {"running", "stopped", "idle", "error", "starting"}

    run_py = client.post("/sandbox/run/python", json={"script": "print('ok')"})
    assert run_py.status_code == 200
    assert "success" in run_py.json()
    assert "usedFallback" in run_py.json()

    stop_all = client.post("/sandbox/stop-all")
    assert stop_all.status_code == 200
    assert stop_all.json()["success"] is True

    with client.stream("POST", "/sandbox/exec/stream", json={"command": "python", "args": ["-c", "print('x')"], "provider": "native"}) as resp:
        assert resp.status_code == 200
        payloads = _read_sse_payloads(resp)
    types = [p.get("type") for p in payloads]
    assert "started" in types
    assert "done" in types


def test_sandbox_uses_provider_config() -> None:
    client = TestClient(app)
    sync = client.post("/providers/settings/sync", json={"sandboxProvider": "codex"})
    assert sync.status_code == 200
    run = client.post("/sandbox/run/python", json={"script": "print('hello')"})
    assert run.status_code == 200
    data = run.json()
    # Codex may be available or may fallback to native depending on environment.
    assert data["provider"] in {"codex", "native", "claude"}


def test_sandbox_run_file_network_packages_auto_native(tmp_path) -> None:
    script = tmp_path / "net.py"
    script.write_text("print('network test')", encoding="utf-8")
    client = TestClient(app)

    # Even when the default provider is configured as codex, network packages
    # should force native when the request does not explicitly pin provider.
    sync = client.post("/providers/settings/sync", json={"sandboxProvider": "codex"})
    assert sync.status_code == 200

    run = client.post(
        "/sandbox/run/file",
        json={
            "filePath": str(script),
            "workDir": str(tmp_path),
            "packages": ["requests"],
        },
    )
    assert run.status_code == 200
    data = run.json()
    assert data["provider"] == "native"


def test_sandbox_pool_stats_endpoint() -> None:
    client = TestClient(app)
    resp = client.get("/sandbox/pool/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert "enabled" in body
    assert isinstance(body.get("pools"), dict)


def test_sandbox_pool_stats_reflects_usage(monkeypatch) -> None:
    monkeypatch.setenv("FORGEPILOT_SANDBOX_POOL_ENABLED", "1")
    client = TestClient(app)

    exec_resp = client.post("/sandbox/exec", json={"command": "python", "args": ["-c", "print('pool')"], "provider": "native"})
    assert exec_resp.status_code == 200

    stats_resp = client.get("/sandbox/pool/stats")
    assert stats_resp.status_code == 200
    pools = stats_resp.json().get("pools") or {}
    assert "native" in pools
    assert pools["native"]["total"] >= 1


def test_files_stat_and_read(tmp_path) -> None:
    f = tmp_path / "hello.txt"
    f.write_text("hello", encoding="utf-8")
    client = TestClient(app)

    stat = client.post("/files/stat", json={"path": str(f)})
    assert stat.status_code == 200
    assert stat.json()["exists"] is True

    read = client.post("/files/read", json={"path": str(f)})
    assert read.status_code == 200
    assert read.json()["success"] is True
    assert read.json()["content"] == "hello"

    readdir = client.post("/files/readdir", json={"path": str(tmp_path), "maxDepth": 2})
    assert readdir.status_code == 200
    assert readdir.json()["success"] is True
    assert isinstance(readdir.json()["files"], list)

    binary = client.post("/files/read-binary", json={"path": str(f)})
    assert binary.status_code == 200
    assert binary.json()["success"] is True


def test_preview_stop_all_and_mcp_path() -> None:
    client = TestClient(app)
    resp = client.post("/preview/stop-all")
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    path_resp = client.get("/mcp/path")
    assert path_resp.status_code == 200
    assert path_resp.json()["success"] is True


def test_agent_permission_endpoint() -> None:
    client = TestClient(app)
    bad = client.post("/agent/permission", json={"sessionId": "s1"})
    assert bad.status_code == 400
    assert bad.json() == {"error": "sessionId and permissionId are required"}

    resp = client.post("/agent/permission", json={"sessionId": "s1", "permissionId": "p1", "approved": True})
    assert resp.status_code == 200
    assert "success" in resp.json()


def test_provider_error_contracts() -> None:
    client = TestClient(app)

    missing_type = client.post("/providers/agents/switch", json={})
    assert missing_type.status_code == 400
    assert missing_type.json() == {"error": "Provider type is required"}

    unknown = client.get("/providers/agents/not-exists")
    assert unknown.status_code == 404
    assert "error" in unknown.json()

    detect_missing = client.post("/providers/detect", json={"baseUrl": "https://example.com"})
    assert detect_missing.status_code == 400
    assert detect_missing.json() == {"error": "baseUrl and apiKey are required"}


def test_files_error_contracts(tmp_path) -> None:
    client = TestClient(app)

    missing = client.post("/files/readdir", json={})
    assert missing.status_code == 400
    assert missing.json() == {"error": "Path is required"}

    file_path = tmp_path / "a.txt"
    file_path.write_text("x", encoding="utf-8")
    not_dir = client.post("/files/readdir", json={"path": str(file_path)})
    assert not_dir.status_code == 400
    assert not_dir.json()["success"] is False
    assert not_dir.json()["error"] == "Path is not a directory"
    assert not_dir.json()["files"] == []

    missing_binary = client.post("/files/read-binary", json={"path": str(tmp_path / "missing.bin")})
    assert missing_binary.status_code == 404
    assert missing_binary.json() == {"error": "File does not exist"}


def test_files_import_skill_contract(monkeypatch, tmp_path) -> None:
    client = TestClient(app)

    missing = client.post("/files/import-skill", json={})
    assert missing.status_code == 400
    assert missing.json() == {"success": False, "error": "url is required"}

    missing_target = client.post(
        "/files/import-skill",
        json={"url": "https://github.com/example/repo"},
    )
    assert missing_target.status_code == 400
    assert missing_target.json() == {"success": False, "error": "targetDir is required"}

    monkeypatch.setattr("forgepilot_api.api.files._is_allowed_path", lambda _p: False)
    denied = client.post(
        "/files/import-skill",
        json={"url": "https://github.com/example/repo", "targetDir": str(tmp_path)},
    )
    assert denied.status_code == 403
    assert denied.json() == {"success": False, "error": "Access denied"}
    monkeypatch.setattr("forgepilot_api.api.files._is_allowed_path", lambda _p: True)

    repo_root = tmp_path / "repo"
    skill_root = repo_root / "awesome-skill"
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: Awesome Skill\ndescription: demo\n---\n# Skill\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "forgepilot_api.api.files._parse_github_repo_url",
        lambda raw_url: ("https://github.com/example/repo.git", None, None),
    )
    monkeypatch.setattr(
        "forgepilot_api.api.files._clone_repo_to_temp",
        lambda clone_url, branch: repo_root,
    )

    target_dir = tmp_path / "skills-out"
    success = client.post(
        "/files/import-skill",
        json={"url": "https://github.com/example/repo", "targetDir": str(target_dir)},
    )
    assert success.status_code == 200
    payload = success.json()
    assert payload["success"] is True
    assert payload["count"] == 1
    assert payload["source"]["cloneUrl"] == "https://github.com/example/repo.git"
    assert payload["source"]["branch"] is None
    imported_path = payload["imported"][0]["path"]
    assert imported_path.endswith("awesome-skill")
    assert (Path(imported_path) / "SKILL.md").exists()

    # Duplicate import should auto-suffix.
    second = client.post(
        "/files/import-skill",
        json={"url": "https://github.com/example/repo", "targetDir": str(target_dir)},
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["success"] is True
    assert second_payload["count"] == 1
    assert second_payload["imported"][0]["name"].startswith("awesome-skill")
    assert second_payload["imported"][0]["name"] != "awesome-skill"

    # Explicit branch/path override support.
    nested_repo = tmp_path / "nested-repo"
    nested_skill = nested_repo / "skills" / "alpha"
    nested_skill.mkdir(parents=True, exist_ok=True)
    (nested_skill / "SKILL.md").write_text("# Nested\n", encoding="utf-8")
    monkeypatch.setattr(
        "forgepilot_api.api.files._clone_repo_to_temp",
        lambda clone_url, branch: nested_repo,
    )
    nested = client.post(
        "/files/import-skill",
        json={
            "url": "https://github.com/example/repo",
            "targetDir": str(target_dir),
            "branch": "dev",
            "path": "skills/alpha",
        },
    )
    assert nested.status_code == 200
    nested_payload = nested.json()
    assert nested_payload["success"] is True
    assert nested_payload["source"]["branch"] == "dev"
    assert nested_payload["source"]["path"] == "skills/alpha"
    assert nested_payload["count"] == 1

    empty_repo = tmp_path / "empty-repo"
    empty_repo.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "forgepilot_api.api.files._clone_repo_to_temp",
        lambda clone_url, branch: empty_repo,
    )
    no_skill = client.post(
        "/files/import-skill",
        json={"url": "https://github.com/example/repo", "targetDir": str(target_dir)},
    )
    assert no_skill.status_code == 400
    assert no_skill.json() == {"success": False, "error": "No SKILL.md found in repository"}


def test_files_import_skill_self_check_contract(monkeypatch, tmp_path) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "forgepilot_api.api.files._parse_github_repo_url",
        lambda raw_url: ("https://github.com/example/repo.git", None, None),
    )

    repo_root = tmp_path / "repo"
    skill_root = repo_root / "skills" / "alpha"
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    monkeypatch.setattr(
        "forgepilot_api.api.files._clone_repo_to_temp",
        lambda clone_url, branch: repo_root,
    )

    success = client.post(
        "/files/import-skill/self-check",
        json={"path": "skills/alpha"},
    )
    assert success.status_code == 200
    payload = success.json()
    assert payload["success"] is True
    assert payload["count"] == 1
    assert payload["source"]["cloneUrl"] == "https://github.com/example/repo.git"
    assert payload["source"]["path"] == "skills/alpha"
    assert payload["sample"]["name"] == "alpha"

    overridden = client.post(
        "/files/import-skill/self-check",
        json={
            "url": "https://github.com/example/repo/tree/main/skills/alpha",
            "branch": "main",
            "path": "skills/alpha",
        },
    )
    assert overridden.status_code == 200
    overridden_payload = overridden.json()
    assert overridden_payload["success"] is True
    assert overridden_payload["source"]["branch"] == "main"
    assert overridden_payload["source"]["path"] == "skills/alpha"

    empty_repo = tmp_path / "empty"
    empty_repo.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "forgepilot_api.api.files._clone_repo_to_temp",
        lambda clone_url, branch: empty_repo,
    )
    no_skill = client.post(
        "/files/import-skill/self-check",
        json={"url": "https://github.com/example/repo"},
    )
    assert no_skill.status_code == 400
    assert no_skill.json() == {"success": False, "error": "No SKILL.md found in repository"}


def test_preview_error_contracts() -> None:
    client = TestClient(app)

    missing_task = client.post("/preview/start", json={"workDir": "."})
    assert missing_task.status_code == 400
    assert missing_task.json() == {"error": "taskId is required"}

    missing_workdir = client.post("/preview/start", json={"taskId": "t1"})
    assert missing_workdir.status_code == 400
    assert missing_workdir.json() == {"error": "workDir is required"}


def test_agent_execute_and_stop_error_contracts() -> None:
    client = TestClient(app)

    no_plan = client.post("/agent/execute", json={"planId": "missing", "prompt": "x"})
    assert no_plan.status_code == 404
    assert no_plan.json() == {"error": "Plan not found or expired"}

    stop_missing = client.post("/agent/stop/not-found")
    assert stop_missing.status_code == 404
    assert stop_missing.json() == {"error": "Session not found"}


def test_sandbox_error_contracts() -> None:
    client = TestClient(app)

    missing_cmd = client.post("/sandbox/exec", json={})
    assert missing_cmd.status_code == 400
    assert missing_cmd.json() == {"error": "Command is required"}

    missing_script = client.post("/sandbox/run/python", json={})
    assert missing_script.status_code == 400
    assert missing_script.json() == {"error": "Script content is required"}

    missing_stream_cmd = client.post("/sandbox/exec/stream", json={})
    assert missing_stream_cmd.status_code == 400
    assert missing_stream_cmd.json() == {"error": "Command is required"}


