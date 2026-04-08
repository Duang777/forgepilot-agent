from __future__ import annotations

from forgepilot_api.services import codex_config_service


def test_load_codex_runtime_config_from_files(monkeypatch, tmp_path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)

    (codex_home / "config.toml").write_text(
        '\n'.join(
            [
                'model_provider = "sub2api"',
                'model = "gpt-5.4"',
                '',
                '[model_providers.sub2api]',
                'name = "sub2api"',
                'base_url = "https://echo.example.com"',
                'wire_api = "responses"',
            ]
        ),
        encoding="utf-8",
    )
    (codex_home / "auth.json").write_text(
        '{"auth_mode":"apikey","OPENAI_API_KEY":"sk-codex-test"}',
        encoding="utf-8",
    )

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("FORGEPILOT_USE_CODEX_CONFIG", "1")

    cfg = codex_config_service.load_codex_runtime_config()
    assert cfg["apiKey"] == "sk-codex-test"
    assert cfg["baseUrl"] == "https://echo.example.com"
    assert cfg["model"] == "gpt-5.4"
    assert cfg["apiType"] == "openai-completions"
    assert cfg["provider"] == "sub2api"


def test_load_codex_runtime_config_can_be_disabled(monkeypatch, tmp_path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "config.toml").write_text('model = "gpt-5.4"', encoding="utf-8")
    (codex_home / "auth.json").write_text('{"OPENAI_API_KEY":"sk-test"}', encoding="utf-8")

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("FORGEPILOT_USE_CODEX_CONFIG", "0")

    assert codex_config_service.load_codex_runtime_config() == {}


def test_load_codex_runtime_config_uses_env_fallback(monkeypatch, tmp_path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "config.toml").write_text(
        '\n'.join(
            [
                'model_provider = "sub2api"',
                '',
                '[model_providers.sub2api]',
                'wire_api = "responses"',
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("FORGEPILOT_USE_CODEX_CONFIG", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.example.com")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    cfg = codex_config_service.load_codex_runtime_config()
    assert cfg["apiKey"] == "sk-env"
    assert cfg["baseUrl"] == "https://env.example.com"
    assert cfg["model"] == "gpt-4o"


def test_parse_minimal_toml_nested_sections() -> None:
    parsed = codex_config_service._parse_minimal_toml(
        '\n'.join(
            [
                'model_provider = "sub2api"',
                '[model_providers.sub2api]',
                'base_url = "https://a.example.com"',
                'requires_openai_auth = true',
            ]
        )
    )
    assert parsed["model_provider"] == "sub2api"
    section = parsed["model_providers"]["sub2api"]
    assert section["base_url"] == "https://a.example.com"
    assert section["requires_openai_auth"] is True


