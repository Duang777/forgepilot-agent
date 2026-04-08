from __future__ import annotations

from fastapi.testclient import TestClient

from forgepilot_api.app import create_app
from forgepilot_api.core.settings import reset_settings_cache


def _build_client() -> TestClient:
    return TestClient(create_app())


def test_files_open_blocked_when_dangerous_endpoints_disabled_in_prod(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FORGEPILOT_FILES_MODE", "prod")
    monkeypatch.setenv("FORGEPILOT_AUTH_MODE", "off")
    reset_settings_cache()
    try:
        sample = tmp_path / "sample.txt"
        sample.write_text("hello", encoding="utf-8")
        monkeypatch.setattr("forgepilot_api.api.files._run_cmd", lambda *args, **kwargs: None)

        client = _build_client()
        response = client.post("/files/open", json={"path": str(sample)})
        assert response.status_code == 403
        assert response.json() == {"error": "__FILES_FEATURE_DISABLED__|files.open"}
    finally:
        reset_settings_cache()


def test_files_open_denied_by_acl_without_open_scope(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FORGEPILOT_FILES_MODE", "prod")
    monkeypatch.setenv("FORGEPILOT_FILES_DANGEROUS_ENABLED", "1")
    monkeypatch.setenv("FORGEPILOT_AUTH_MODE", "off")
    reset_settings_cache()
    try:
        sample = tmp_path / "sample.txt"
        sample.write_text("hello", encoding="utf-8")
        monkeypatch.setattr("forgepilot_api.api.files._run_cmd", lambda *args, **kwargs: None)

        client = _build_client()
        response = client.post("/files/open", json={"path": str(sample)})
        assert response.status_code == 403
        assert response.json() == {"error": "__FILES_ACL_DENIED__|files.open"}
    finally:
        reset_settings_cache()


def test_files_open_allowed_for_subject_scope(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FORGEPILOT_FILES_MODE", "prod")
    monkeypatch.setenv("FORGEPILOT_FILES_DANGEROUS_ENABLED", "1")
    monkeypatch.setenv("FORGEPILOT_AUTH_MODE", "api_key")
    monkeypatch.setenv("FORGEPILOT_API_KEYS", "operator:open-key")
    monkeypatch.setenv("FORGEPILOT_FILES_ACL_SUBJECTS", "operator=files.open")
    reset_settings_cache()
    try:
        sample = tmp_path / "sample.txt"
        sample.write_text("hello", encoding="utf-8")
        monkeypatch.setattr("forgepilot_api.api.files._run_cmd", lambda *args, **kwargs: None)

        client = _build_client()
        response = client.post(
            "/files/open",
            json={"path": str(sample)},
            headers={"x-api-key": "open-key"},
        )
        assert response.status_code == 200
        assert response.json() == {"success": True}
    finally:
        reset_settings_cache()


def test_files_import_denied_without_import_scope(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FORGEPILOT_FILES_MODE", "prod")
    monkeypatch.setenv("FORGEPILOT_FILES_DANGEROUS_ENABLED", "1")
    monkeypatch.setenv("FORGEPILOT_AUTH_MODE", "api_key")
    monkeypatch.setenv("FORGEPILOT_API_KEYS", "viewer:view-key")
    monkeypatch.setenv("FORGEPILOT_FILES_ACL_SUBJECTS", "viewer=files.read")
    reset_settings_cache()
    try:
        client = _build_client()
        response = client.post(
            "/files/import-skill",
            json={
                "url": "https://github.com/example/repo",
                "targetDir": str(tmp_path / "skills"),
            },
            headers={"x-api-key": "view-key"},
        )
        assert response.status_code == 403
        assert response.json() == {"error": "__FILES_ACL_DENIED__|files.import_skill"}
    finally:
        reset_settings_cache()
