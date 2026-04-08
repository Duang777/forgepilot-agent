from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from forgepilot_api.services.codex_config_service import load_codex_runtime_config
from forgepilot_api.sandbox.registry import get_sandbox_registry
from forgepilot_api.storage.repositories import read_settings, write_setting


@dataclass(slots=True)
class ProviderState:
    sandbox_type: str = "codex"
    sandbox_config: dict[str, Any] = field(default_factory=dict)
    agent_type: str = "codeany"
    agent_config: dict[str, Any] = field(default_factory=dict)
    default_provider: str = ""
    default_model: str = ""


_state = ProviderState()
_initialized = False
_agent_config_seeded_from_codex = False

AGENT_METADATA = [
    {"type": "codeany", "name": "CodeAny Agent", "description": "In-process Open Agent SDK runtime"},
    {"type": "custom", "name": "Custom Agent", "description": "Custom external runtime"},
]


def _attach_status(metadata: list[dict[str, str]], current_type: str, available: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "type": m["type"],
            "name": m["name"],
            "description": m.get("description", ""),
            "available": m["type"] in available,
            "current": m["type"] == current_type,
        }
        for m in metadata
    ]


async def ensure_loaded() -> None:
    global _initialized, _agent_config_seeded_from_codex
    if _initialized:
        return
    settings = await read_settings()
    _state.sandbox_type = str(settings.get("sandboxProvider") or _state.sandbox_type)
    _state.sandbox_config = settings.get("sandboxConfig") or {}
    _state.agent_type = str(settings.get("agentProvider") or _state.agent_type)
    _state.agent_config = settings.get("agentConfig") or {}
    _state.default_provider = str(settings.get("defaultProvider") or _state.default_provider)
    _state.default_model = str(settings.get("defaultModel") or _state.default_model)
    _agent_config_seeded_from_codex = False

    # If frontend hasn't synced agent credentials yet, bootstrap from local Codex runtime config.
    codex_cfg = load_codex_runtime_config()
    if not _state.agent_config:
        seeded: dict[str, Any] = {}
        if codex_cfg.get("apiKey"):
            seeded["apiKey"] = str(codex_cfg["apiKey"])
        if codex_cfg.get("baseUrl"):
            seeded["baseUrl"] = str(codex_cfg["baseUrl"])
        if codex_cfg.get("model"):
            seeded["model"] = str(codex_cfg["model"])
        if codex_cfg.get("apiType"):
            seeded["apiType"] = str(codex_cfg["apiType"])
        if seeded:
            _state.agent_config = seeded
            _agent_config_seeded_from_codex = True
    if not _state.default_model and codex_cfg.get("model"):
        _state.default_model = str(codex_cfg["model"])

    _initialized = True


async def _persist() -> None:
    await write_setting("sandboxProvider", _state.sandbox_type)
    await write_setting("sandboxConfig", _state.sandbox_config)
    await write_setting("agentProvider", _state.agent_type)
    await write_setting("agentConfig", _state.agent_config)
    await write_setting("defaultProvider", _state.default_provider)
    await write_setting("defaultModel", _state.default_model)


async def get_available_sandbox_providers() -> list[str]:
    registry = get_sandbox_registry()
    return await registry.get_available()


async def get_available_agent_providers() -> list[str]:
    return ["codeany", "custom"]


async def get_sandbox_providers() -> dict[str, Any]:
    await ensure_loaded()
    registry = get_sandbox_registry()
    metadata = registry.get_metadata()
    available = await get_available_sandbox_providers()
    return {"providers": _attach_status(metadata, _state.sandbox_type, available), "current": _state.sandbox_type}


async def get_agent_providers() -> dict[str, Any]:
    await ensure_loaded()
    available = await get_available_agent_providers()
    return {"providers": _attach_status(AGENT_METADATA, _state.agent_type, available), "current": _state.agent_type}


async def switch_sandbox_provider(provider_type: str, config: dict[str, Any] | None = None) -> None:
    await ensure_loaded()
    _state.sandbox_type = provider_type
    _state.sandbox_config = config or {}
    await _persist()


async def switch_agent_provider(provider_type: str, config: dict[str, Any] | None = None) -> None:
    global _agent_config_seeded_from_codex
    await ensure_loaded()
    _state.agent_type = provider_type
    _state.agent_config = config or {}
    _agent_config_seeded_from_codex = False
    await _persist()


async def sync_settings(body: dict[str, Any]) -> dict[str, Any]:
    global _agent_config_seeded_from_codex
    await ensure_loaded()
    if body.get("sandboxProvider"):
        _state.sandbox_type = str(body["sandboxProvider"])
        _state.sandbox_config = body.get("sandboxConfig") or {}
    if body.get("agentProvider"):
        _state.agent_type = str(body["agentProvider"])
        _state.agent_config = body.get("agentConfig") or {}
        _agent_config_seeded_from_codex = False
    if "defaultProvider" in body:
        _state.default_provider = str(body["defaultProvider"] or "")
    if "defaultModel" in body:
        _state.default_model = str(body["defaultModel"] or "")
    await _persist()
    return await get_config()


async def get_config() -> dict[str, Any]:
    await ensure_loaded()
    if _state.agent_config:
        config_source = "codex" if _agent_config_seeded_from_codex else "settings"
    else:
        config_source = "empty"
    return {
        "sandbox": {"category": "sandbox", "type": _state.sandbox_type, "config": _state.sandbox_config},
        "agent": {"category": "agent", "type": _state.agent_type, "config": _state.agent_config},
        "agentConfigSource": config_source,
        "defaultProvider": _state.default_provider,
        "defaultModel": _state.default_model,
    }

