from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from typing import Any

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}

_cached_key: tuple[str, float, float] | None = None
_cached_value: dict[str, str | None] = {}


def _codex_enabled() -> bool:
    raw = os.getenv("FORGEPILOT_USE_CODEX_CONFIG", "1").strip().lower()
    if raw in _FALSY:
        return False
    return True


def _parse_toml_value(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return ""
    if text.startswith('"') and text.endswith('"'):
        try:
            return ast.literal_eval(text)
        except Exception:
            return text.strip('"')
    lower = text.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    try:
        if "." in text:
            return float(text)
        return int(text)
    except Exception:
        return text


def _strip_inline_comment(line: str) -> str:
    in_string = False
    escaped = False
    for idx, ch in enumerate(line):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if ch == "#" and not in_string:
            return line[:idx].rstrip()
    return line


def _parse_minimal_toml(text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    section_parts: list[str] = []

    for raw_line in text.splitlines():
        line = _strip_inline_comment(raw_line).strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            section_parts = [p.strip() for p in section.split(".") if p.strip()]
            continue
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        parsed = _parse_toml_value(value)

        target: dict[str, Any] = data
        for part in section_parts:
            child = target.get(part)
            if not isinstance(child, dict):
                child = {}
                target[part] = child
            target = child
        target[key] = parsed

    return data


def _read_codex_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    return _parse_minimal_toml(text)


def _read_auth_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _as_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _infer_api_type(model: str | None, wire_api: str | None) -> str:
    wire = (wire_api or "").strip().lower()
    if wire in {"responses", "chat_completions", "chat-completions", "openai-completions"}:
        return "openai-completions"
    if wire in {"anthropic-messages", "messages"}:
        return "anthropic-messages"

    lowered = (model or "").lower()
    if "claude" in lowered:
        return "anthropic-messages"
    return "openai-completions"


def load_codex_runtime_config() -> dict[str, str | None]:
    if not _codex_enabled():
        return {}

    codex_home = Path(os.getenv("CODEX_HOME") or (Path.home() / ".codex"))
    config_path = codex_home / "config.toml"
    auth_path = codex_home / "auth.json"

    config_mtime = config_path.stat().st_mtime if config_path.exists() else 0.0
    auth_mtime = auth_path.stat().st_mtime if auth_path.exists() else 0.0
    cache_key = (str(codex_home), config_mtime, auth_mtime)

    global _cached_key, _cached_value
    if _cached_key == cache_key:
        return dict(_cached_value)

    config = _read_codex_toml(config_path)
    auth = _read_auth_json(auth_path)

    provider_name = _as_optional_text(config.get("model_provider"))
    providers = config.get("model_providers")
    provider_cfg = providers.get(provider_name) if isinstance(providers, dict) and provider_name else {}
    if not isinstance(provider_cfg, dict):
        provider_cfg = {}

    model = (
        _as_optional_text(config.get("model"))
        or _as_optional_text(os.getenv("CODEX_MODEL"))
        or _as_optional_text(os.getenv("OPENAI_MODEL"))
    )
    base_url = (
        _as_optional_text(provider_cfg.get("base_url"))
        or _as_optional_text(os.getenv("CODEX_BASE_URL"))
        or _as_optional_text(os.getenv("OPENAI_BASE_URL"))
        or _as_optional_text(os.getenv("OPENAI_API_BASE"))
    )
    wire_api = _as_optional_text(provider_cfg.get("wire_api"))
    api_key = (
        _as_optional_text(auth.get("OPENAI_API_KEY"))
        or _as_optional_text(auth.get("DUANGCODE_API_KEY"))
        or _as_optional_text(auth.get("CODEANY_API_KEY"))
        or _as_optional_text(os.getenv("OPENAI_API_KEY"))
        or _as_optional_text(os.getenv("DUANGCODE_API_KEY"))
        or _as_optional_text(os.getenv("DUANGCODE_AUTH_TOKEN"))
        or _as_optional_text(os.getenv("CODEANY_API_KEY"))
        or _as_optional_text(os.getenv("CODEANY_AUTH_TOKEN"))
    )

    resolved = {
        "apiKey": api_key,
        "baseUrl": base_url,
        "model": model,
        "apiType": _infer_api_type(model, wire_api),
        "provider": provider_name,
    }

    _cached_key = cache_key
    _cached_value = dict(resolved)
    return resolved
